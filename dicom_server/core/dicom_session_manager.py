from datetime import datetime
from dependency_injector.wiring import inject
from services.redis_service import IRedisService
from services.dicom_session_service import ISessionCollector
from services.threat_intelligence_service import IThreatIntelligence
import time, pytz
import utilities.dicom_util as dicom_util
from enums.dicom_session_keys import Sessionkeys as sk


class SessionCollector(ISessionCollector):
    @inject
    def __init__(
        self,
        app_logger,
        simp_logger,
        exceptions_logger,
        redis_handler: IRedisService = None,
        threat_intelligence: IThreatIntelligence = None,
    ):

        self.session_info = {key.key: key.default for key in sk}
        self.redis_data = {}
        self.redis_handler = redis_handler or IRedisService()
        self.simp_logger = simp_logger
        self.exceptions_logger = exceptions_logger
        self.threat_intelligence = threat_intelligence
        self.logger = app_logger
        self.timezone = pytz.timezone("Europe/Copenhagen")

    def session_started(self, ip, port, v_name):
        """
        Starting a new DICOM session
        whenever an association request recieved

        """
        try:

            if not self.session_locked():
                self.logger.warning(
                    f"\033[93mDICOM session started at {datetime.now(self.timezone).strftime('%Y-%m-%dT%H:%M:%S')}\n\033[0mInitializing session info for host: \033[92m{ip}\033[0m port: \033[92m{port}\033[0m"
                )
                self.initialize_session_info(ip, port)
                self.get_session_requestor_reputation(ip)
                self.set_session_lock(1)
            else:
                self.session_info[sk.VERSION.key] = v_name
                self.logger.info(
                    f"Requester DICOM client version: \033[92m{v_name}\033[0m"
                )
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while starting a DICOM session"
            )

    def get_session_requestor_reputation(self, ip):
        """
        Using the data-access provider "redis_handler" to populate data to redis

        """
        try:
            rep_dat = {}
            ip_scanned = self.redis_handler.is_ip_scanned(ip)
            if not ip_scanned:
                rep_dat = self.threat_intelligence.get_reputation_data(ip)
                self.redis_handler.add_reputation_data(rep_dat)
                self.redis_handler.add_scanned_ip(ip)
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while getting session requestor reputation data"
            )

    def initialize_session_info(self, ip, port):
        """
        Initializing session information by setting session keys values

        """
        try:
            current_time = time.time()
            known_scanner = dicom_util.is_known_scanner(ip)
            self.session_info[sk.KNOWN_SCANNER.key] = known_scanner
            self.session_info[sk.SESSION_MAIN_OPERATION.key] = "Association Requested"
            self.session_info[sk.REQUEST_TYPE.key] = "Association Requested"
            self.session_info[sk.IP.key] = str(ip)
            self.session_info[sk.PORT.key] = port
            self.session_info[sk.LOG_LEVEL.key] = "Warning"
            self.set_session_id(str(int(current_time * 1000)))
            self.collect_session_info({}, True)
            self.logger.info(
                f'Host identified as a \033[92m"Known_scanner"\033'
                if known_scanner
                else 'Host identified as a\033[91m"Non_known_scanner"\033[0m\nHost reputation data can be found on the Redis server or the visualization dashbord'
            )
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while initializing DICOM session"
            )

    def collect_session_info(self, params, sub_process_finished=False):
        """
        Collecting session information through the dicom_handlers provider
        If sub_process_finished is True means a dicom request in this session is elapsed then we log a simplified message

        """
        try:
            for key, value in params.items():
                self.session_info[key] = value
            if sub_process_finished:

                current_time = datetime.now(self.timezone).strftime(
                    "%Y-%m-%dT%H:%M:%S.%f"
                )
                self.session_info[sk.TIMESTAMP.key] = str(current_time)
                self.simp_logger.info(self.session_info)
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while collecting DICOM session information"
            )

    def session_ended(self):
        """
        push session data to Redis and reset the session information object

        """
        try:

            self.redis_handler.add_request_data(self.build_redis_object())
            self.logger.info(
                f'Main operation:\033[92m"{self.session_info[sk.SESSION_MAIN_OPERATION.key]}" \033[0m Status:\033[92m"{self.session_info[sk.STATUS.key]}"\033[0m'
            )
            self.logger.warning(
                f"\033[93mDICOM session ended at {datetime.now(self.timezone).strftime('%Y-%m-%dT%H:%M:%S')}\n\033[0m"
            )
            self.reset_session()
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while ending a DICOM session"
            )

    def reset_session(self):
        self.session_info = {key.key: key.default for key in sk}

    def build_redis_object(self):
        """
        build a Redis object and filtering the not log-stash relevant keys

        """

        try:
            self.session_info[sk.STATUS.key] = (
                "Finished"
                if self.session_info[sk.REQUEST_TYPE.key] == "Association Released"
                else "Aborted"
            )
            redis_object = self.session_info.copy()
            redis_object[sk.REQUEST_TYPE.key] = self.session_info[
                sk.SESSION_MAIN_OPERATION.key
            ]
            keys_to_remove = {sk.LOCK.key, sk.SESSION_MAIN_OPERATION.key}
            for key in keys_to_remove:
                redis_object.pop(key, None)
            js_redis = dicom_util.format_log_entry(str(redis_object))
            self.logger.debug(f"Redis object formatted: {js_redis}")
            return js_redis
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while building Redis object"
            )

    def set_session_lock(self, value):
        self.session_info[sk.LOCK.key] = value

    def session_locked(self):

        return self.session_info[sk.LOCK.key] == 1

    def set_session_id(self, s_id):
        self.session_info[sk.SESSION_ID.key] = s_id
