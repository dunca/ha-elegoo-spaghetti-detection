"""Constants for Elegoo spaghetti detection."""

from homeassistant.const import Platform

DOMAIN = "elegoo_spaghetti_detection"
BRAND = "Elegoo Spaghetti Detection"

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.SWITCH,
]

CONF_INSTANCE_ID = "instance_id"
CONF_HOME_ASSISTANT_HOST = "home_assistant_host"
CONF_OBICO_HOST = "obico_host"
CONF_OBICO_AUTH_TOKEN = "obico_auth_token"
CONF_CAMERA = "camera"
CONF_SNAPSHOT_URL = "snapshot_url"
CONF_PRINT_STATUS_SENSOR = "print_status_sensor"
CONF_ACTIVE_PRINT_STATES = "active_print_states"
CONF_CHAMBER_LIGHT = "chamber_light"
CONF_REQUIRE_LIGHT = "require_light"
CONF_LIGHT_CONTROL_MODE = "light_control_mode"
CONF_LIGHT_SETTLE_SECONDS = "light_settle_seconds"
CONF_DETECTION_INTERVAL = "detection_interval"
CONF_RUN_WITHOUT_PRINTING = "run_without_printing"
CONF_FAILURE_THRESHOLD = "failure_threshold"
CONF_WARNING_THRESHOLD = "warning_threshold"
CONF_SENSITIVITY = "sensitivity"
CONF_COOLDOWN_SECONDS = "cooldown_seconds"
CONF_IMAGE_URL = "image_url"
CONF_CONFIG_ENTRY = "config_entry"
CONF_DETECTOR = "detector"
CONF_FORCE = "force"
CONF_FRAMES_ENABLED = "frames_enabled"
CONF_FRAMES_KEEP_PRINTS = "frames_keep_prints"
CONF_FRAMES_MAX_PER_PRINT = "frames_max_per_print"
CONF_FRAMES_MAX_MB = "frames_max_mb"
CONF_JOB_ID_ENTITY = "job_id_entity"
CONF_JOB_NAME_ENTITY = "job_name_entity"
CONF_PRINT_ID = "print_id"

DEFAULT_NAME = "Elegoo Spaghetti Detector"
DEFAULT_INSTANCE_ID = DOMAIN
DEFAULT_HOME_ASSISTANT_HOST = "http://homeassistant.local:8123"
DEFAULT_OBICO_HOST = "http://192.168.1.123:3333"
DEFAULT_OBICO_AUTH_TOKEN = "obico_api_secret"
DEFAULT_ACTIVE_PRINT_STATES = "printing"
DEFAULT_DETECTION_INTERVAL = 10
DEFAULT_COOLDOWN_SECONDS = 900
DEFAULT_FAILURE_THRESHOLD = 0.50
DEFAULT_WARNING_THRESHOLD = 0.30
DEFAULT_SENSITIVITY = "normal"
DEFAULT_LIGHT_CONTROL_MODE = "restore"
DEFAULT_LIGHT_SETTLE_SECONDS = 3
DEFAULT_REQUIRE_LIGHT = False
DEFAULT_FRAMES_ENABLED = True
DEFAULT_FRAMES_KEEP_PRINTS = 10
DEFAULT_FRAMES_MAX_PER_PRINT = 1500
DEFAULT_FRAMES_MAX_MB = 1500

LIGHT_CONTROL_OFF = "off"
LIGHT_CONTROL_LEAVE_ON = "leave_on"
LIGHT_CONTROL_RESTORE = "restore"

# Every value the Status sensor can take.
STATUS_IDLE = "idle"
STATUS_WAITING_FOR_PRINT = "waiting_for_print"
STATUS_UNAVAILABLE = "status_unavailable"
STATUS_LIGHT_OFF = "light_off"
STATUS_DISABLED = "disabled"
STATUS_CHECKING = "checking"
STATUS_CLEAR = "clear"
STATUS_WARNING = "warning"
STATUS_DETECTED = "detected"
STATUS_ERROR = "error"
STATUS_BUSY = "busy"
STATUS_OPTIONS = [
    STATUS_IDLE,
    STATUS_WAITING_FOR_PRINT,
    STATUS_UNAVAILABLE,
    STATUS_LIGHT_OFF,
    STATUS_DISABLED,
    STATUS_CHECKING,
    STATUS_CLEAR,
    STATUS_WARNING,
    STATUS_DETECTED,
    STATUS_ERROR,
    STATUS_BUSY,
]

# Every value the Last result sensor can take: the outcome of the last
# completed check, or none before the first one.
LAST_RESULT_NONE = "none"
LAST_RESULT_OPTIONS = [
    LAST_RESULT_NONE,
    STATUS_CLEAR,
    STATUS_WARNING,
    STATUS_DETECTED,
    STATUS_ERROR,
]

# Detection counts as stalled when no check has completed for this many
# detection intervals, and never sooner than STALL_MIN_SECONDS.
STALL_INTERVALS = 3
STALL_MIN_SECONDS = 90

REQUIRED_CONFIG_KEYS = frozenset(
    {
        CONF_INSTANCE_ID,
        CONF_HOME_ASSISTANT_HOST,
        CONF_OBICO_HOST,
        CONF_OBICO_AUTH_TOKEN,
        CONF_CAMERA,
    }
)

SENSITIVITY_THRESHOLDS = {
    "high": (0.20, 0.35),
    "normal": (DEFAULT_WARNING_THRESHOLD, DEFAULT_FAILURE_THRESHOLD),
    "low": (0.45, 0.70),
    "custom": (DEFAULT_WARNING_THRESHOLD, DEFAULT_FAILURE_THRESHOLD),
}

EVENT_DETECTION_RESULT = f"{DOMAIN}_result"
EVENT_SPAGHETTI_DETECTED = f"{DOMAIN}_detected"

SERVICE_PREDICT = "predict"
SERVICE_RUN_DETECTION = "run_detection"
SERVICE_RESET_STATE = "reset_state"
SERVICE_DELETE_FRAMES = "delete_frames"

RUNTIME_DATA = "runtime"
RUNTIME_BY_DETECTOR = "runtime_by_detector"
RUNTIME_ML_LOCK = "ml_lock"

ATTR_CONFIDENCE = "confidence"
ATTR_RAW_SCORE = "raw_score"
ATTR_DETECTED = "detected"
ATTR_DETECTIONS = "detections"
ATTR_IMAGE_URL = "image_url"
ATTR_LAST_ERROR = "last_error"
ATTR_LAST_RUN = "last_run"
ATTR_NEXT_RUN = "next_run"
ATTR_STATUS = "status"
ATTR_FRAME = "frame"
ATTR_PRINT_ID = "print_id"
ATTR_AT = "at"
