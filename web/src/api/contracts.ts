/** Public API wire contracts. JSON field names remain snake_case. */

/**
 * @asType integer
 * @minimum 0
 */
export type Count = number;
/**
 * @asType integer
 * @minimum 1
 */
export type DatabaseId = number;
/**
 * @asType integer
 * @minimum 1
 * @maximum 50
 */
export type SnapshotLimit = number;
/** An ISO 8601 timestamp supplied by the server. */
export type Timestamp = string;
export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };
/** Extensible metadata and preferences intentionally retain arbitrary JSON values. */
export type JsonRecord = { [key: string]: JsonValue };

export interface SuccessEnvelope {
  success: true;
  message?: string;
  category?: 'success';
}
export interface FieldError { path: string; message: string }
export interface ApiError {
  success: false;
  error_code: string;
  message: string;
  category: string;
  retryable?: boolean;
  retry_after?: number;
  field_errors?: FieldError[];
  operation_id?: DatabaseId;
}
export type ApiResponse<T extends SuccessEnvelope> = T | ApiError;

export type AuthNoticeCode = 'access_denied' | 'oauth_failed' | 'missing_code' | 'invalid_state'
  | 'authentication_failed' | 'consent_required' | 'login_failed';
/** A fixed-map notice is consumed from the session once. No OAuth query text is returned. */
export interface AuthNotice {
  code: AuthNoticeCode;
  category: 'error' | 'success' | 'info';
  message: string;
}
export interface SessionUser { id: string; display_name: string | null; image_url: string | null }
export interface SignedOutSession extends SuccessEnvelope {
  signed_in: false;
  user: null;
  notice: AuthNotice | null;
}
export interface SignedInSession extends SuccessEnvelope {
  signed_in: true;
  user: SessionUser;
  /** Present only when signed in; never persisted in fixtures or logs from real sessions. */
  csrf_token: string;
  notice: AuthNotice | null;
}
export type SessionContext = SignedOutSession | SignedInSession;

export interface ShuffleParams {
  keep_first?: Count;
  section_count?: Count;
  shuffle_percentage?: number;
  shuffle_location?: 'front' | 'back';
  min_spacing?: Count;
  jitter?: Count;
  shuffle_within_albums?: 'no' | 'yes';
}
export interface RoutineParams extends ShuffleParams {
  rotation_mode?: 'swap';
  rotation_count?: Count;
  target_size?: Count;
  protect_count?: Count;
  drip_count?: Count;
}
export interface NumberParameter {
  type: 'integer' | 'float';
  description: string;
  default: number;
  min?: number;
  max?: number;
}
export interface StringParameter {
  type: 'string';
  description: string;
  default: string;
  options: string[];
}
export type AlgorithmParameter = NumberParameter | StringParameter;
export interface Algorithm {
  name: string;
  class_name: string;
  description: string;
  parameters: { [name: string]: AlgorithmParameter };
}
export interface AlgorithmOption { value: string; label: string }
export type Theme = 'light' | 'dark' | 'system';
export interface UserSettings {
  id: DatabaseId;
  user_id: DatabaseId;
  default_algorithm: string | null;
  /** Stored preferences retain parameters from custom or unavailable algorithms. */
  default_algorithm_params: JsonRecord;
  theme: Theme;
  notifications_enabled: boolean;
  auto_snapshot_enabled: boolean;
  max_snapshots_per_playlist: SnapshotLimit;
  dashboard_show_recent_activity: boolean;
  extra: JsonRecord;
  created_at: Timestamp | null;
  updated_at: Timestamp | null;
}
/** GET and POST /api/settings return this same complete context. */
export interface SettingsContext extends SuccessEnvelope {
  settings: UserSettings;
  algorithms: Algorithm[];
  algorithm_options: AlgorithmOption[];
}
export interface SettingsUpdateRequest {
  default_algorithm?: string | null;
  default_algorithm_params?: JsonRecord;
  theme?: Theme;
  notifications_enabled?: boolean;
  auto_snapshot_enabled?: boolean;
  max_snapshots_per_playlist?: SnapshotLimit;
  dashboard_show_recent_activity?: boolean;
  extra?: JsonRecord;
}

export type JobType = 'raid' | 'shuffle' | 'raid_and_shuffle' | 'raid_and_drip' | 'rotate' | 'drip';
export type TriggeredBy = 'manual' | 'scheduler' | null;
export type ExecutionStepName = 'raid' | 'shuffle' | 'rotate' | 'drip';
export type ExecutionStepStatus = 'complete' | 'partial' | 'unknown' | 'failed' | 'restored' | 'not_started' | 'pending';
export type FactStatus = 'complete' | 'failed' | 'unknown' | 'restored' | 'not_required';
export interface ExecutionCounts {
  tracks_collected?: Count;
  tracks_moved_to_target?: Count;
  tracks_removed_from_collection?: Count;
  tracks_reordered?: Count;
  tracks_archived?: Count;
  tracks_returned?: Count;
  archive_overlaps_removed?: Count;
  tracks_removed_from_target?: Count;
  tracks_removed_from_archive?: Count;
}
export interface ExecutionResourceFact {
  resource: string;
  status: FactStatus;
  phase?: ExecutionStepName | 'archive_purge';
  kind?: 'target' | 'collection' | 'archive';
  mutation_type?: 'track_order' | 'track_membership';
  affected_count?: Count;
  dispatched: boolean;
}
export interface ExecutionBookkeepingFact {
  kind: 'pending_stage' | 'pending_promotion' | 'pending_restore' | 'execution_record';
  status: FactStatus;
  resource?: string;
  affected_count?: Count;
}
export interface ExecutionStep {
  step: ExecutionStepName;
  status: ExecutionStepStatus;
  counts?: ExecutionCounts;
  /** @maxItems 12 */
  resources: ExecutionResourceFact[];
  /** @maxItems 12 */
  bookkeeping: ExecutionBookkeepingFact[];
}
export interface UnavailableExecutionDetail {
  detail_status: 'unavailable';
  version: null;
  steps: null;
}
export interface AvailableExecutionDetail {
  detail_status: 'available';
  version: 1;
  operation: JobType;
  execution_id: DatabaseId;
  outcome: 'complete' | 'partial' | 'unknown';
  /**
   * @minItems 1
   * @maxItems 4
   */
  steps: ExecutionStep[];
  run_state?: 'running' | 'finished';
  resource_ids?: { target?: string; collection?: string; archive?: string };
  execution_record?: 'complete' | 'unknown';
}
export type ExecutionDetail = UnavailableExecutionDetail | AvailableExecutionDetail;
export interface JobExecution {
  id: DatabaseId;
  schedule_id: DatabaseId | null;
  started_at: Timestamp | null;
  completed_at: Timestamp | null;
  status: string;
  tracks_added: Count | null;
  tracks_total: Count | null;
  error_message: string | null;
  result_detail: ExecutionDetail;
}
export interface ActivityExecution extends JobExecution {
  schedule_name: string | null;
  job_type: JobType | 'unknown';
  /** Null means no persisted origin is known; readers must never guess an origin. */
  triggered_by: TriggeredBy;
}
export interface Activity {
  id: DatabaseId;
  user_id: DatabaseId;
  activity_type: string;
  description: string;
  playlist_id: string | null;
  playlist_name: string | null;
  metadata: JsonRecord | null;
  created_at: Timestamp | null;
  triggered_by: TriggeredBy;
}
export interface QuickStats {
  total_shuffles: Count;
  total_scheduled_runs: Count;
  total_snapshots: Count;
  active_schedule_count: Count;
}
export interface ActivityContext extends SuccessEnvelope {
  stats: QuickStats;
  /** @maxItems 100 */
  activities: Activity[];
  /** @maxItems 20 */
  executions: ActivityExecution[];
}

/** Shared playlist projection used by the library and user-playlist picker. */
export interface PlaylistSummary {
  id: string;
  name: string;
  image_url: string | null;
  track_count: Count;
  spotify_url: string;
}
export interface LibraryRow extends PlaylistSummary {
  public: boolean | null;
  pinned: boolean;
  hidden: boolean;
  can_undo: boolean;
}
export interface PlaylistRef { id: string; name: string }
export interface UserPlaylistsContext extends SuccessEnvelope { playlists: PlaylistSummary[] }
export interface ShuffleDefaults { algorithm: string | null; params: ShuffleParams }
export interface LibraryGreeting {
  is_new_user: boolean;
  is_returning_user: boolean;
  display_name: string | null;
  activity_since_last_login_count: Count;
}
export interface RoutineDefinition {
  job_type: JobType;
  target_playlist_id: string;
  target_playlist_name: string | null;
  source_playlist_ids: string[];
  algorithm_name: string | null;
  /** Persisted routines retain parameters from custom or unavailable algorithms. */
  algorithm_params: JsonRecord;
  schedule_type: 'interval' | 'cron';
  schedule_value: string;
  timezone: string | null;
}
export interface Schedule extends RoutineDefinition {
  id: DatabaseId;
  user_id: DatabaseId;
  is_enabled: boolean;
  last_run_at: Timestamp | null;
  last_status: string | null;
  last_error: string | null;
  created_at: Timestamp | null;
  updated_at: Timestamp | null;
}
export interface Dashboard {
  is_returning_user: boolean;
  recent_activity: Activity[];
  activity_since_last_login: Activity[];
  activity_since_last_login_count: Count;
  quick_stats: QuickStats;
  active_schedules: Schedule[];
  recent_job_executions: ActivityExecution[];
}
export interface LibraryContext extends SuccessEnvelope {
  /** Arrays retain preference order; the three section names are fixed. */
  sections: { favorites: LibraryRow[]; playlists: LibraryRow[]; hidden: LibraryRow[] };
  algorithms: Algorithm[];
  shuffle_defaults: ShuffleDefaults;
  greeting: LibraryGreeting;
  dashboard: Dashboard;
}

export interface TimingBase {
  timezone: string | null;
  text: string;
  schedule_type: 'interval' | 'cron';
  schedule_value: string;
  effective_timezone: string | null;
  legacy: boolean;
  explanation: string | null;
}
export interface IntervalTiming extends TimingBase {
  kind: 'interval';
  interval: 'every_6h' | 'every_12h' | 'daily' | 'every_3d' | 'weekly';
}
export interface DailyTiming extends TimingBase { kind: 'daily'; time: string }
export interface WeeklyTiming extends TimingBase {
  kind: 'weekly';
  time: string;
  weekday: 'mon' | 'tue' | 'wed' | 'thu' | 'fri' | 'sat' | 'sun';
}
export interface AdvancedTiming extends TimingBase { kind: 'advanced'; expression: string }
export type RoutineTiming = IntervalTiming | DailyTiming | WeeklyTiming | AdvancedTiming;
export interface RoutineRegistration {
  status: 'unavailable' | 'paused' | 'registered' | 'needs_attention';
  next_run_at: Timestamp | null;
  effective_timezone: string | null;
}
export interface RoutineRow {
  schedule_id: DatabaseId;
  schedule: Schedule;
  definition: RoutineDefinition;
  schedule_revision: string;
  timing: RoutineTiming;
  registration: RoutineRegistration;
  state: 'active' | 'paused' | 'needs_attention';
  state_label: 'Active' | 'Paused' | 'Saved—registration needs attention';
}
export interface RoutinesContext extends SuccessEnvelope { routines: RoutineRow[]; playlists: PlaylistRef[] }
/** Navigation intent crosses the API boundary as a code, never as a URL. */
export type ReturnTarget = 'playlist' | 'routines' | 'routine' | 'library';

export interface WorkshopPlaylist {
  id: string;
  name: string;
  owner_id: string;
  description: string | null;
  total_tracks: Count | null;
  public: boolean | null;
  /** Spotify audio feature fields are an intentionally extensible record. */
  audio_features: { [track_id: string]: JsonRecord };
}
export interface WorkshopTrack {
  id: string;
  name: string;
  uri: string;
  duration_ms: Count | null;
  is_local: boolean;
  added_at: Timestamp | null;
  artists: (string | null)[];
  artist_urls: (string | null)[];
  album_name: string | null;
  album_image_url: string | null;
  track_url: string | null;
}
export interface UpstreamSource {
  id: DatabaseId;
  user_id: DatabaseId;
  target_playlist_id: string;
  source_playlist_id: string | null;
  source_url: string | null;
  source_type: 'own' | 'external' | 'search_query';
  source_name: string | null;
  search_query: string | null;
  last_resolved_at: Timestamp | null;
  last_resolve_pathway: string | null;
  last_resolve_status: string | null;
  last_track_count: Count | null;
  raid_count: Count;
  created_at: Timestamp | null;
}
export interface RecentlyLoadedPlaylist {
  id: string;
  name: string;
  owner_id: string;
  track_count: Count | null;
}
export interface WorkshopContext extends SuccessEnvelope {
  playlist: WorkshopPlaylist;
  tracks: WorkshopTrack[];
  algorithms: Algorithm[];
  upstream_sources: { [playlist_id: string]: UpstreamSource[] };
  previous: PlaylistRef | null;
  next: PlaylistRef | null;
  /** @maxItems 10 */
  recently_loaded: RecentlyLoadedPlaylist[];
}

export type OperationState = 'queued' | 'running' | 'compensating' | 'succeeded'
  | 'failed' | 'rolled_back' | 'needs_attention';
export interface OperationProgress {
  batches_done?: Count;
  total_batches?: Count;
  phase?: 'forward' | 'compensating';
}
export interface OperationRecovery {
  available: boolean;
  action: 'restore' | null;
  reason: string | null;
}
/** Private commands, credentials, baselines, and lease tokens never cross this boundary. */
export interface Operation {
  id: DatabaseId;
  kind: string;
  state: OperationState;
  targets: string[];
  started_at: Timestamp | null;
  completed_at: Timestamp | null;
  progress: OperationProgress;
  result: JsonRecord | null;
  recovery: OperationRecovery;
}
export interface OperationContext extends SuccessEnvelope { operation: Operation }
export interface OperationAccepted {
  success: true;
  category: 'info';
  message: string;
  operation_id: DatabaseId;
  operation: Operation;
}
