from argparse import ArgumentParser
from json import load
from pathlib import Path
from subprocess import DEVNULL, TimeoutExpired, run
from sys import executable, stdout
from time import monotonic, sleep

from loguru import logger
from requests import RequestException, Session

from login import CAMPUS_CONFIGS, Manager


PROJECT_DIR = Path(__file__).resolve().parent
PROBE_URL = "http://www.msftconnecttest.com/connecttest.txt"
PROBE_CONTENT = "Microsoft Connect Test"


def positive_integer(value):
    number = int(value)
    if number < 1:
        raise ValueError("Value must be positive")
    return number


def internet_available(session):
    try:
        with session.get(
            PROBE_URL, timeout=5, allow_redirects=False, stream=True,
            headers={"Cache-Control": "no-cache"}
        ) as response:
            if response.status_code != 200:
                return False
            content = next(response.iter_content(chunk_size=128), b"")
            return content.strip() == PROBE_CONTENT.encode()
    except (RequestException, OSError):
        return False


def authenticate_if_offline(campus, login_timeout):
    try:
        with Manager(campus=campus) as manager:
            status = manager.check()
    except (Exception, SystemExit) as error:
        logger.warning("Portal unavailable ({}); waiting for next check", type(error).__name__)
        return False
    if status.get("error") == "ok":
        logger.warning("Portal reports online; skipping authentication despite probe failure")
        return False
    if status.get("error") != "not_online_error":
        logger.warning("Portal did not confirm offline status; skipping authentication")
        return False
    logger.info("Portal reports offline; starting authentication")
    try:
        result = run(
            [executable, str(PROJECT_DIR / "login.py"), "--campus", campus, "--once"],
            cwd=PROJECT_DIR, stdout=DEVNULL, stderr=DEVNULL, timeout=login_timeout
        )
    except TimeoutExpired:
        logger.warning("Authentication exceeded its time limit; retrying after cooldown")
        return False
    except OSError as error:
        logger.warning("Could not start authentication ({})", type(error).__name__)
        return False
    if result.returncode != 0:
        logger.warning("Authentication failed; retrying after cooldown")
        return False
    logger.info("Authentication completed; connectivity will be checked again")
    return True


def validate_auth_file():
    with (PROJECT_DIR / "auth.json").open(encoding="utf-8") as auth_file:
        credentials = load(auth_file)
    if not isinstance(credentials, list) or not credentials:
        raise ValueError("Expected a non-empty account list")
    for credential in credentials:
        if not isinstance(credential, dict) or any(
            not isinstance(credential.get(field), str) or not credential[field]
            for field in ("username", "password")
        ):
            raise ValueError("Expected non-empty username and password strings")


def monitor(session, campus, interval, failures, cooldown, login_timeout):
    failed_checks = 0
    next_attempt = 0
    was_offline = False
    while True:
        if internet_available(session):
            if was_offline:
                logger.info("Internet connectivity restored")
            failed_checks = 0
            was_offline = False
        else:
            failed_checks += 1
            if not was_offline:
                logger.warning("Connectivity probe failed; checking before authentication")
                was_offline = True
            if failed_checks >= failures and monotonic() >= next_attempt:
                authenticate_if_offline(campus, login_timeout)
                next_attempt = monotonic() + cooldown
                failed_checks = 0
        sleep(interval)


def main():
    parser = ArgumentParser(description="Reauthenticate HDU network after connectivity loss")
    parser.add_argument("--campus", choices=CAMPUS_CONFIGS, default="shaoxing")
    parser.add_argument("--interval", type=positive_integer, default=15)
    parser.add_argument("--failures", type=positive_integer, default=3)
    parser.add_argument("--cooldown", type=positive_integer, default=60)
    parser.add_argument("--login-timeout", type=positive_integer, default=60)
    args = parser.parse_args()
    logger.remove()
    logger.add(stdout, level="INFO", diagnose=False, backtrace=False)
    logger.add(PROJECT_DIR / "reconnect.log", rotation="10 MB", diagnose=False, backtrace=False)
    try:
        validate_auth_file()
    except (OSError, ValueError):
        logger.error("Please create a valid auth.json alongside reconnect.py")
        return 1
    logger.info("Connectivity monitor started for {} campus", args.campus)
    try:
        with Session() as session:
            session.trust_env = False
            monitor(session, args.campus, args.interval, args.failures, args.cooldown, args.login_timeout)
    except KeyboardInterrupt:
        logger.info("Connectivity monitor stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
