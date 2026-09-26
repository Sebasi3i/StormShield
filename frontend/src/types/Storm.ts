export interface StormTrackPoint {
  step: number
  timestamp: string
  latitude: number
  longitude: number
  max_wind_kt: number
  category: string
  is_over_land: boolean
}

export interface Storm {
  storm_id: string
  genesis_time: string
  peak_wind_kt: number
  duration_hours: number
  landfall: boolean
  landfall_time: string
  landfall_lat: number
  landfall_lon: number
  landfall_wind_kt: number
  track: StormTrackPoint[]
}

export interface StormCatalog {
  catalog_id: string
  storm_ids: string[]
  storms: Storm[]
}