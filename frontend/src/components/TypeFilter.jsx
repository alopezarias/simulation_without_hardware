import { TYPE_FILTERS } from '../utils'

export default function TypeFilter({ active, onChange }) {
  return (
    <div className="flex gap-2 overflow-x-auto pb-1 scrollbar-none">
      {TYPE_FILTERS.map(({ value, label }) => {
        const isActive = active === value
        return (
          <button
            key={String(value)}
            onClick={() => onChange(value)}
            className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-colors
              ${isActive
                ? 'bg-indigo-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'}`}
          >
            {label}
          </button>
        )
      })}
    </div>
  )
}
