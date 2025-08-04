import os, logging, json, sys
from datetime import datetime
from services.loggers_service import ILoggers
import traceback


class SimplifiedLogsFormatter(logging.Formatter):
    def __init__(self, is_production):
        super().__init__(style="%")
        self.is_production = is_production

    def format(self, record):
        msg = str(record.msg)

        log_object = json.loads(
            msg.replace("'", '"').replace("False", "false").replace("True", "true")
        )

        keys_to_remove = {"lock", "main_operation", "known_scanner", "status"}
        for key in keys_to_remove:
            log_object.pop(key, None)
        if (
            log_object["request_type"] == "Association Aborted"
            or log_object["request_type"] == "Association Released"
        ):
            # Remove none subprocess info
            log_object["session_parameters"] = "N/A"
            log_object["query_level"] = "N/A"
            log_object["matches"] = "N/A"

        return json.dumps(log_object, default=str)


class ExceptionFormatter(logging.Formatter):
    def __init__(self, is_production, app_logger):
        super().__init__(style="%")
        self.is_production = is_production
        self.logger = app_logger

    def format(self, record):

        excep = (
            "Exception at "
            + datetime.now().strftime("%d-%m-%Y : %H-%M-%S")
            + "\n.............................................\n"
        )
        trace = ""

        if record.exc_info:
            trace = "".join(traceback.format_exception(*record.exc_info))

            tb = record.exc_info[2]
            while tb.tb_next:
                tb = tb.tb_next
            frame = tb.tb_frame
            excep += "In " + frame.f_globals["__name__"] + "\n"

            self.logger.error(
                "Exception in "
                + frame.f_globals["__name__"]
                + "\n"
                + "Message: "
                + record.msg
                + "\nSee\033[91m exceptions.log \033[0m file for more details\n.......................................\n"
            )

        excep += (
            "Message: "
            + str(record.msg)
            + "\n"
            + "Traceback :\n ............\n"
            + trace
        )
        return excep


class Loggers(ILoggers):

    def __init__(
        self,
        is_production,
        main_logger_directory,
        simplified_log_directory,
        exception_log_directory,
    ):

        self.is_production = is_production
        self.app_logger = self.setup_logger("app_logger", None)
        self.main_logger = self.setup_logger(
            "pynetdicom", main_logger_directory, logging.DEBUG, None
        )
        self.simplified_logger = self.setup_logger(
            "simplified_logger",
            simplified_log_directory,
            logging.INFO,
            SimplifiedLogsFormatter(is_production),
        )
        self.exceptions_logger = self.setup_logger(
            "exceptions",
            exception_log_directory,
            logging.ERROR,
            ExceptionFormatter(is_production, self.app_logger),
        )

    def setup_logger(
        self,
        name,
        log_directory,
        level=logging.INFO,
        formatter=None,
    ):
        if name != "app_logger":
            if log_directory:
                os.makedirs(log_directory, exist_ok=True)
            
            handler = logging.FileHandler(
                os.path.join(log_directory, name + ".log")
            )
            handler.setFormatter(formatter)
            stream_handler = logging.StreamHandler(stream=sys.stdout)
            logger = logging.getLogger(name)
            logger.setLevel(level)
            if not self.is_production:
                logger.addHandler(stream_handler)
            logger.addHandler(handler)
            self.app_logger.debug(f'Logger: {name} initialized at "{log_directory}"')
            return logger
        else:
            logger = logging.getLogger("app_logger")
            handler = logging.StreamHandler(sys.stdout)
            if self.is_production:
                logger.setLevel(logging.INFO)
                handler.setLevel(logging.INFO)
            else:
                logger.setLevel(logging.DEBUG)
                handler.setLevel(logging.DEBUG)
            formatter = logging.Formatter("%(levelname)s - %(message)s")
            handler.setFormatter(formatter)
            handler.addFilter(self.add_color)
            logger.addHandler(handler)
            return logger

    def add_color(self, record):
        colors = {
            "DEBUG": "\033[94m",  # Blue
            "INFO": "\033[92m",  # Green
            "WARNING": "\033[93m",  # Yellow
            "ERROR": "\033[91m",  # Red
            "CRITICAL": "\033[1;91m",  # Bold Red
        }
        reset = "\033[0m"
        levelname = record.levelname
        if levelname in colors:
            record.levelname = colors[levelname] + levelname + reset
        return True
