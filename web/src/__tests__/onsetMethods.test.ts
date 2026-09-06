/**
 * Verifies that the ONSET_METHODS constant in lib/api.ts contains exactly the
 * three values that sync_engine.py accepts.  A mismatch here means the UI can
 * store an onset_method string that the backend silently ignores (it falls
 * through to cava-based detection without an error).
 *
 * When adding a new onset method to the backend, add it here too.  When the
 * test fails, the fix is NOT to update only the test — update the constant
 * so both sides match.
 */
import { describe, it, expect } from 'vitest'
import { ONSET_METHODS } from '../lib/api'

const BACKEND_ACCEPTED: ReadonlySet<string> = new Set([
  'combined',
  'multiband',
  'superflux',
])

describe('ONSET_METHODS constant', () => {
  it('contains exactly the values accepted by the backend', () => {
    const frontendValues = new Set(ONSET_METHODS.map((m) => m.value))
    expect(frontendValues).toEqual(BACKEND_ACCEPTED)
  })

  it('has no duplicate values', () => {
    const values = ONSET_METHODS.map((m) => m.value)
    expect(new Set(values).size).toBe(values.length)
  })

  it('every entry has a non-empty label', () => {
    for (const m of ONSET_METHODS) {
      expect(m.label.length, `${m.value} has empty label`).toBeGreaterThan(0)
    }
  })
})
