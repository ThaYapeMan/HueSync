/**
 * Generalized guard for enum-like dropdown constants.
 *
 * Each entry in FIELD_SPECS describes one canonical constant: the set of values
 * the backend accepts and the frontend must show.  Adding a new guarded field:
 * 1. Add its constant to lib/api.ts.
 * 2. Add a row here.
 * 3. Add it to models.py and test_enum_fields.py on the backend.
 */
import { describe, it, expect } from 'vitest'
import { ONSET_METHODS, COLOUR_MODES } from '../lib/api'

interface FieldSpec {
  name: string
  constant: ReadonlyArray<{ value: string; label: string }>
  expectedValues: ReadonlySet<string>
}

const FIELD_SPECS: FieldSpec[] = [
  {
    name: 'ONSET_METHODS',
    constant: ONSET_METHODS,
    expectedValues: new Set(['combined', 'multiband', 'superflux']),
  },
  {
    name: 'COLOUR_MODES',
    constant: COLOUR_MODES,
    expectedValues: new Set(['spectrum_rgb', 'mono_pulse']),
  },
]

describe('enum-like dropdown constants', () => {
  for (const spec of FIELD_SPECS) {
    describe(spec.name, () => {
      it('contains exactly the backend-accepted values', () => {
        const actual = new Set(spec.constant.map((m) => m.value))
        expect(actual).toEqual(spec.expectedValues)
      })

      it('has no duplicate values', () => {
        const values = spec.constant.map((m) => m.value)
        expect(new Set(values).size).toBe(values.length)
      })

      it('every entry has a non-empty label', () => {
        for (const m of spec.constant) {
          expect(m.label.length, `${m.value} has empty label`).toBeGreaterThan(0)
        }
      })
    })
  }
})
