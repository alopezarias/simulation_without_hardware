export default function DeviceStatus({ wsStatus, deviceMode }) {
  const isConnected = wsStatus === 'connected'

  const dot = isConnected ? 'bg-green-400 animate-pulse' : 'bg-red-500'
  const label = isConnected
    ? (deviceMode ? deviceMode.toUpperCase() : 'LIVE')
    : wsStatus === 'connecting' ? 'CONECTANDO…' : 'OFFLINE'

  return (
    <div className="flex items-center gap-1.5 text-xs font-mono">
      <span className={`w-2 h-2 rounded-full ${dot}`} />
      <span className={isConnected ? 'text-green-400' : 'text-red-400'}>{label}</span>
    </div>
  )
}
