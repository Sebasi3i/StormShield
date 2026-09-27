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
  landfall_time: string | null
  landfall_lat: number | null
  landfall_lon: number | null
  landfall_wind_kt: number | null
  track: StormTrackPoint[]
  member_seed?: number
  // Set on storms from a Florida batch: the centre was over Florida land at
  // Category 3 or stronger at some track point.
  florida_hit?: boolean
  florida_peak_wind_kt?: number | null
  florida_first_time?: string | null
}

export interface StormCatalog {
  catalog_id: string
  storm_ids: string[]
  storms: Storm[]
  completeness_warning: string
}

export interface StormStart {
  latitude: number
  longitude: number
  max_wind_kt: number
  date: string
}

export interface GenerateFloridaStormsRequest {
  seed: number
  count: number
  max_wind_kt: number
  min_florida_hits: number
}

export interface FloridaStormBatch extends StormCatalog {
  generator: {
    version: string
    seed: number
    count: number
    member_seeds: number[]
    season_year: number
    start: StormStart
  }
  florida: {
    min_wind_kt: number
    criterion: string
    min_hits_requested: number
    hits: string[]
    starts_tried: number
    max_starts: number
  }
}
