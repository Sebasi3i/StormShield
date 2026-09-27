export interface Property {
  id: number
  address: string
  city: string
  county: string
  latitude: number
  longitude: number
  value: number
  // Sent to the API as-is (snake_case is the API's field name): which building code
  // the home was built to, and its roof shape. Both are the demo's assigned
  // placeholders, not measured data.
  vulnerability_class: 'pre_fbc_2002' | 'post_fbc_2002'
  roof_shape: 'gable' | 'hip' | 'unknown'
}
