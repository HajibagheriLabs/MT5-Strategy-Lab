import { createContext, useContext } from 'react'
import type { ReactNode } from 'react'

export type ToastTone = 'info' | 'done' | 'error'
export type ToastInput = { title: ReactNode; message?: ReactNode; tone?: ToastTone }

export const ToastContext = createContext<{ push: (toast: ToastInput) => void } | null>(null)

export function useToast() {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast needs a ToastProvider above it.')
  return context
}
