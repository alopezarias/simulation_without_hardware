"""Local speech helpers: Whisper STT + local TTS with PCM16 output."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import threading
import wave
from typing import Any

import numpy as np

try:
    import soundfile as sf

    SOUND_FILE_AVAILABLE = True
except Exception:
    sf = None  # type: ignore[assignment]
    SOUND_FILE_AVAILABLE = False

try:
    from faster_whisper import WhisperModel

    FASTER_WHISPER_AVAILABLE = True
except Exception:
    WhisperModel = Any  # type: ignore[assignment]
    FASTER_WHISPER_AVAILABLE = False

try:
    import pyttsx3

    PYTTSX3_AVAILABLE = True
except Exception:
    pyttsx3 = None  # type: ignore[assignment]
    PYTTSX3_AVAILABLE = False


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class SpeechPipeline:
    """Thread-safe local STT/TTS utilities."""

    def __init__(self) -> None:
        self.logger = logging.getLogger("speech-pipeline")

        self.whisper_enabled = _env_bool("ENABLE_WHISPER_STT", True)
        self.whisper_model_size = os.getenv("WHISPER_MODEL_SIZE", "base").strip() or "base"
        self.whisper_device = os.getenv("WHISPER_DEVICE", "auto").strip() or "auto"
        self.whisper_compute_type = os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip() or "int8"
        self.whisper_beam_size = max(1, int(os.getenv("WHISPER_BEAM_SIZE", "1")))
        self.whisper_language = os.getenv("WHISPER_LANGUAGE", "es").strip() or None
        self.whisper_vad_filter = _env_bool("WHISPER_VAD_FILTER", True)

        self.tts_enabled = _env_bool("ENABLE_LOCAL_TTS", True)
        self.tts_rate = max(80, min(320, int(os.getenv("TTS_RATE", "175"))))
        self.tts_volume = max(0.0, min(1.0, float(os.getenv("TTS_VOLUME", "1.0"))))
        self.tts_voice = os.getenv("TTS_VOICE", "").strip()
        self.tts_backend = os.getenv("TTS_BACKEND", "auto").strip().lower() or "auto"
        self.say_binary = shutil.which("say")
        self.say_available = bool(self.say_binary)

        self._whisper_model: WhisperModel | None = None
        self._model_lock = threading.Lock()
        self._transcribe_lock = threading.Lock()
        self._tts_lock = threading.Lock()

    @property
    def stt_available(self) -> bool:
        return self.whisper_enabled and FASTER_WHISPER_AVAILABLE

    @property
    def tts_available(self) -> bool:
        if not self.tts_enabled:
            return False
        if self.tts_backend == "say":
            return self.say_available and SOUND_FILE_AVAILABLE
        if self.tts_backend == "pyttsx3":
            return PYTTSX3_AVAILABLE
        return (self.say_available and SOUND_FILE_AVAILABLE) or PYTTSX3_AVAILABLE

    def capabilities(self) -> dict[str, Any]:
        return {
            "stt_enabled": self.whisper_enabled,
            "stt_available": self.stt_available,
            "stt_model": self.whisper_model_size,
            "tts_enabled": self.tts_enabled,
            "tts_available": self.tts_available,
            "tts_backend": self.tts_backend,
            "tts_voice": self.tts_voice or "default",
        }

    def _load_whisper_model(self) -> WhisperModel:
        if not self.stt_available:
            raise RuntimeError(
                "Whisper STT unavailable. Install faster-whisper and enable ENABLE_WHISPER_STT."
            )

        if self._whisper_model is not None:
            return self._whisper_model

        with self._model_lock:
            if self._whisper_model is None:
                self.logger.info(
                    "Loading Whisper model '%s' device=%s compute_type=%s",
                    self.whisper_model_size,
                    self.whisper_device,
                    self.whisper_compute_type,
                )
                self._whisper_model = WhisperModel(
                    self.whisper_model_size,
                    device=self.whisper_device,
                    compute_type=self.whisper_compute_type,
                )

        return self._whisper_model

    def _pcm_to_wav(self, pcm_path: str, sample_rate: int, channels: int) -> str:
        fd, wav_path = tempfile.mkstemp(prefix="sim_stt_", suffix=".wav")
        os.close(fd)
        with open(pcm_path, "rb") as src, wave.open(wav_path, "wb") as dst:
            dst.setnchannels(max(1, channels))
            dst.setsampwidth(2)  # PCM16
            dst.setframerate(max(8000, sample_rate))
            while True:
                chunk = src.read(64 * 1024)
                if not chunk:
                    break
                dst.writeframes(chunk)
        return wav_path

    def transcribe_pcm_file(self, pcm_path: str, sample_rate: int, channels: int) -> str:
        model = self._load_whisper_model()
        wav_path = self._pcm_to_wav(pcm_path, sample_rate=sample_rate, channels=channels)
        try:
            kwargs: dict[str, Any] = {
                "beam_size": self.whisper_beam_size,
                "vad_filter": self.whisper_vad_filter,
            }
            if self.whisper_language:
                kwargs["language"] = self.whisper_language

            with self._transcribe_lock:
                segments, _info = model.transcribe(wav_path, **kwargs)
                text_parts = [segment.text.strip() for segment in segments if segment.text.strip()]

            return " ".join(text_parts).strip()
        finally:
            try:
                os.remove(wav_path)
            except FileNotFoundError:
                pass

    def _synthesize_with_pyttsx3(self, text: str, output_path: str) -> None:
        if pyttsx3 is None:
            raise RuntimeError("pyttsx3 not available")

        with self._tts_lock:
            engine = pyttsx3.init()
            try:
                engine.setProperty("rate", self.tts_rate)
                engine.setProperty("volume", self.tts_volume)

                if self.tts_voice:
                    for voice in engine.getProperty("voices"):
                        voice_id = str(getattr(voice, "id", "")).strip()
                        voice_name = str(getattr(voice, "name", "")).strip()
                        needle = self.tts_voice.lower()
                        if needle in voice_id.lower() or needle in voice_name.lower():
                            engine.setProperty("voice", voice_id)
                            break

                engine.save_to_file(text, output_path)
                engine.runAndWait()
            finally:
                try:
                    engine.stop()
                except Exception:
                    pass

    def _synthesize_with_say(self, text: str, output_path: str) -> None:
        if not self.say_binary:
            raise RuntimeError("'say' command is not available on this system")

        command = [self.say_binary, "-o", output_path]
        if self.tts_voice:
            command.extend(["-v", self.tts_voice])
        command.extend(["-r", str(self.tts_rate)])
        command.append(text)
        subprocess.run(command, check=True, capture_output=True, text=True)

    def _synthesize_to_file(self, text: str, output_path: str) -> None:
        if not self.tts_enabled:
            raise RuntimeError("Local TTS is disabled (ENABLE_LOCAL_TTS=false)")

        order: list[str]
        if self.tts_backend in {"say", "pyttsx3"}:
            order = [self.tts_backend]
        else:
            order = ["say", "pyttsx3"]

        errors: list[str] = []
        for backend in order:
            try:
                if backend == "say":
                    self._synthesize_with_say(text, output_path)
                    return
                self._synthesize_with_pyttsx3(text, output_path)
                return
            except Exception as exc:
                errors.append(f"{backend}: {exc}")

        raise RuntimeError("All TTS backends failed -> " + " | ".join(errors))

    def _audio_to_pcm16(
        self,
        input_path: str,
        output_pcm_path: str,
        target_sample_rate: int,
        target_channels: int,
    ) -> int:
        if SOUND_FILE_AVAILABLE and sf is not None:
            samples, src_rate = sf.read(input_path, dtype="float32", always_2d=True)
            if samples.ndim != 2 or samples.shape[1] < 1:
                raise RuntimeError("Invalid audio produced by TTS backend")
            pcm = samples
            src_channels = pcm.shape[1]
        else:
            with wave.open(input_path, "rb") as src:
                src_channels = int(src.getnchannels())
                src_width = int(src.getsampwidth())
                src_rate = int(src.getframerate())
                src_frames = int(src.getnframes())
                raw = src.readframes(src_frames)

            if src_channels < 1:
                raise RuntimeError(f"Invalid TTS channel count: {src_channels}")

            if src_width == 1:
                pcm_raw = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
                pcm_raw = (pcm_raw - 128.0) / 128.0
            elif src_width == 2:
                pcm_raw = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            elif src_width == 4:
                pcm_raw = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
            else:
                raise RuntimeError(f"Unsupported sample width from TTS: {src_width}")

            if src_channels > 1:
                pcm = pcm_raw.reshape(-1, src_channels)
            else:
                pcm = pcm_raw.reshape(-1, 1)

        if target_channels == 1:
            mixed = np.mean(pcm.astype(np.float32), axis=1)
            pcm = mixed.reshape(-1, 1)
        elif target_channels == src_channels:
            pass
        elif target_channels == 2 and src_channels == 1:
            mono = pcm[:, 0].astype(np.float32)
            pcm = np.stack([mono, mono], axis=1)
        else:
            raise RuntimeError(f"Cannot convert channels from {src_channels} to {target_channels}")

        if src_rate != target_sample_rate:
            if len(pcm) == 0:
                resampled = np.zeros((0, target_channels), dtype=np.float32)
            else:
                src_points = np.arange(len(pcm), dtype=np.float32)
                dst_length = max(1, int(round(len(pcm) * target_sample_rate / src_rate)))
                dst_points = np.linspace(0, len(pcm) - 1, num=dst_length, dtype=np.float32)
                channels: list[np.ndarray] = []
                for channel_index in range(pcm.shape[1]):
                    channel = np.interp(dst_points, src_points, pcm[:, channel_index].astype(np.float32))
                    channels.append(channel)
                resampled = np.stack(channels, axis=1)
            pcm = resampled
        else:
            pcm = pcm.astype(np.float32)

        pcm = np.clip(pcm, -1.0, 1.0)
        pcm = np.round(pcm * 32767.0).astype(np.int16)
        payload = pcm.tobytes()
        with open(output_pcm_path, "wb") as dst:
            dst.write(payload)

        return len(payload)

    def synthesize_text_to_pcm_file(
        self,
        text: str,
        target_sample_rate: int,
        target_channels: int,
    ) -> tuple[str, int]:
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            raise ValueError("Cannot synthesize empty text")

        fd_pcm, pcm_path = tempfile.mkstemp(prefix="sim_tts_", suffix=".pcm")
        os.close(fd_pcm)

        fd_tmp, tts_audio_path = tempfile.mkstemp(prefix="sim_tts_src_", suffix=".aiff")
        os.close(fd_tmp)
        try:
            self._synthesize_to_file(cleaned, tts_audio_path)
            total_bytes = self._audio_to_pcm16(
                tts_audio_path,
                pcm_path,
                target_sample_rate=max(8000, target_sample_rate),
                target_channels=max(1, target_channels),
            )
        finally:
            try:
                os.remove(tts_audio_path)
            except FileNotFoundError:
                pass

        return pcm_path, total_bytes
