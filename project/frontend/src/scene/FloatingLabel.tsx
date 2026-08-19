import { Html } from '@react-three/drei'

interface FloatingLabelProps {
  name: string
  state: string
  position: [number, number, number]
  color?: string
}

export function FloatingLabel({ name, state, position, color = '#26A3DD' }: FloatingLabelProps) {
  return (
    <Html position={position} center distanceFactor={12} style={{ pointerEvents: 'none' }}>
      <div
        style={{
          fontFamily: "'Hanken Grotesk', sans-serif",
          fontSize: '11px',
          fontWeight: 700,
          letterSpacing: '0.04em',
          color,
          textShadow: '0 0 6px rgba(0, 28, 100, 0.5), 0 1px 2px rgba(0, 28, 100, 0.6)',
          whiteSpace: 'nowrap',
          userSelect: 'none',
        }}
      >
        {name}[{state}]
      </div>
    </Html>
  )
}

export function statusColor(state: string): string {
  if (state === 'OK' || state === 'ONLINE' || state === 'OPEN') return '#26A3DD'
  if (state === 'DAMAGED' || state === 'OFFLINE' || state === 'CLOSED') return '#ba1a1a'
  return '#26A3DD'
}
