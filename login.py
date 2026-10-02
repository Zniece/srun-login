from argparse import ArgumentParser
from datetime import datetime
from hashlib import sha1
from ipaddress import IPv4Address
from json import loads, dumps, load
from random import choice
from re import compile
from sys import stdout
from time import time, sleep

from apscheduler.schedulers.blocking import BlockingScheduler
from loguru import logger
from requests import Session

from utils.base import b64encode
from utils.device import devices
from utils.hash import md5
from utils.xencode import xencode

AUTH_FILE = "auth.json"
INVALID_AUTH_ERROR = "4xx"
RETRY_DELAY = 2
CAMPUS = "xiasha"
CAMPUS_CONFIGS = {
    "xiasha": {
        "hosts": [
            "https://login.hdu.edu.cn", "https://portal.hdu.edu.cn",
            "http://192.168.112.30", "http://192.168.112.97"
        ],
        "ac_id": 0,
    },
    "shaoxing": {"hosts": ["https://yue.hdu.edu.cn"], "ac_id": 1},
}


def parse_jsonp(text: str, callback: str) -> dict:
    text = text.strip().removesuffix(";").rstrip()
    prefix = callback + "("
    if text.startswith(prefix) and text.endswith(")"):
        text = text[len(prefix):-1]
    return loads(text)


headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 "
                  " Safari/537.36 Edg/128.0.0.0"
}
auths = []


class Manager(Session):

    def __init__(self, username: str = "", password: str = "", campus: str = None):
        super().__init__()
        self.campus = campus or CAMPUS
        self.config = CAMPUS_CONFIGS[self.campus]
        self.acid: int = self.config["ac_id"]
        self.n: str = "200"
        self.vtype: str = "1"
        self.enc_ver: str = "srun_bx1"
        self.username = username
        self.password = password
        self.logger = logger
        self.host = self.get_host()
        self.token, self.checksum, self.info = None, None, None

    def get_host(self):
        hosts = self.config["hosts"]
        for i in hosts:
            try:
                self.get(i, timeout=10).raise_for_status()
                return i
            except Exception as e:
                self.logger.info(f"Host {i} {e}")
        self.logger.error("Failed to get host...")
        exit(-1)

    def get_ip(self) -> str:
        if self.campus == "shaoxing":
            response = self.get(
                self.host + "/srun_portal_pc", headers=headers,
                params={"ac_id": self.acid, "theme": "hdu-yue"}, timeout=10
            )
            response.raise_for_status()
            match = compile(r'''\bip\s*:\s*["']([0-9.]+)["']''').search(response.text)
            if match is None:
                raise ValueError("Failed to find client IP in Shaoxing portal page")
            return str(IPv4Address(match.group(1)))
        resp = self.get(self.host + "/srun_portal_pc", headers=headers, timeout=10).text
        try:
            ip = compile(r'((1\d{2}|25[0-5]|2[0-4]\d|[1-9]?\d)\.){3}(25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)').search(
                resp).group()
        except AttributeError:
            self.logger.error("Failed to get IP")
            ip = self.get_ip()
        return ip

    def get_token(self) -> str:
        callback = f"jQuery1124015280105355320628_{round(time() * 1000)}"
        params = {
            "callback": callback,
            "username": self.username,
            "ip": self.get_ip(),
            "_": round(time() * 1000)
        }
        resp = self.get(self.host + "/cgi-bin/get_challenge", headers=headers, params=params, timeout=10).text
        token = parse_jsonp(resp, callback)["challenge"]
        return token

    def get_info(self) -> str:
        return "{SRBX1}" + b64encode(xencode(dumps({
            "username": self.username,
            "password": self.password,
            "ip": self.get_ip(),
            "acid": str(self.acid),
            "enc_ver": self.enc_ver,
        }), self.token))

    def get_checksum(self) -> str:
        checksum = self.token + self.username
        checksum += self.token + md5(self.password, self.token)
        checksum += self.token + str(self.acid)
        checksum += self.token + self.get_ip()
        checksum += self.token + self.n
        checksum += self.token + self.vtype
        checksum += self.token + self.info
        return sha1(checksum.encode()).hexdigest()

    def login(self) -> dict:
        self.token = self.get_token()
        self.info = self.get_info()
        self.checksum = self.get_checksum()
        callback = f"jQuery1124015280105355320628_{round(time() * 1000)}"
        device = choice(devices)
        params = {
            "callback": callback,
            "action": "login",
            "username": self.username,
            "password": "{MD5}" + md5(self.password, self.token),
            'os': device[0],
            'name': device[1],
            "double_stack": "0",
            "chksum": self.checksum,
            "info": self.info,
            "ac_id": str(self.acid),
            "ip": self.get_ip(),
            "n": self.n,
            "type": self.vtype,
            "_": round(time() * 1000)
        }
        resp = self.get(self.host + "/cgi-bin/srun_portal", headers=headers, params=params, timeout=10).text
        result = parse_jsonp(resp, callback)
        if result.get("suc_msg"):
            self.logger.success(f'login: {result["suc_msg"]}')
        else:
            self.logger.error(f'login failed: {result.get("error")}')
            error_message = result.get("error_msg") or ""
            if "BAS" in error_message or "Nas" in error_message:
                """
                INFO failed, BAS respond timeout.
                Nas type not found.
                """
                self.logger.error("ac_id error, retry in 5 seconds...")
                self.acid += 1
                sleep(5)
                result = self.login()
            elif "E2901" in error_message:
                """
                E2901: (Third party -200)ldap_first_entry error
                E2901: (Third party 1)bind_user2: ldap_bind error
                """
                self.logger.error("username or password error...")
                result["error_msg"] = "4xx"
            elif "E2606" in error_message:
                """
                E2606: User is disabled.
                """
                self.logger.error("user is disabled...")
                result["error_msg"] = "4xx"
        return result

    def logout(self) -> dict:
        callback = f"jQuery112405185119642573086_{round(time() * 1000)}"
        t = round(time())
        status = self.check()
        username = status.get("user_name") if status.get("user_name") else self.username
        ip = status.get("online_ip") if status.get("online_ip") else self.get_ip()
        params = {
            "callback": callback,
            "username": username,
            "ip": ip,
            "time": t,
            "unbind": "1",
            "sign": sha1(f"{t}{username}{ip}1{t}".encode()).hexdigest(),
            "_": round(time() * 1000)
        }
        resp = self.get(self.host + "/cgi-bin/rad_user_dm", headers=headers, params=params, timeout=10).text
        result = parse_jsonp(resp, callback)
        self.logger.info(f'logout: {result.get("error")}')
        return result

    def check(self) -> dict:
        callback = f"jQuery112405185119642573086_{round(time() * 1000)}"
        params = {
            "callback": callback,
            "_": round(time() * 1000)
        }
        resp = self.get(self.host + "/cgi-bin/rad_user_info", headers=headers, params=params, timeout=10).text
        result = parse_jsonp(resp, callback)
        self.logger.info(f'check: {result.get("error")}')
        return result


def refresh():
    logger.debug("Try to refresh...")
    manager = Manager(*get_random_auth())
    manager.logout()
    retry_login(manager)


def check():
    logger.debug("Check status...")
    manager = Manager()
    status = manager.check()
    if status.get("error") != "ok":
        logger.warning(f"{status.get('error')}, try to login...")
        retry_login(manager, refresh_auth_before_login=True)


def get_random_auth():
    auth = choice(auths)
    return auth["username"], auth["password"]


def set_random_auth(manager: Manager):
    manager.username, manager.password = get_random_auth()


def retry_login(manager: Manager, refresh_auth_before_login: bool = False):
    while True:
        if refresh_auth_before_login:
            set_random_auth(manager)
        result = manager.login()
        if result.get("error_msg") != INVALID_AUTH_ERROR:
            return result
        if not refresh_auth_before_login:
            set_random_auth(manager)
        logger.debug(f"username or password is incorrect, retry in {RETRY_DELAY} seconds...")
        sleep(RETRY_DELAY)


def setup_logger():
    logger.remove()
    logger.add(
        "srun_login.log", rotation="10 MB", level="DEBUG",
        format="<g>{time:MM-DD HH:mm:ss}</g> [<lvl>{level}</lvl>] <c><u>srun_login</u></c> | {message}"
    )
    logger.add(
        stdout, level="INFO",
        format="<g>{time:MM-DD HH:mm:ss}</g> [<lvl>{level}</lvl>] <c><u>srun_login</u></c> | {message}"
    )


def load_auths():
    try:
        with open(AUTH_FILE, "r", encoding="utf-8") as file:
            return load(file)
    except Exception as e:
        logger.bind(module="srun_login").error(f"{e}, please check {AUTH_FILE}")
        exit(-1)


def start_scheduler():
    scheduler = BlockingScheduler()
    scheduler.add_job(refresh, 'interval', hours=6)
    scheduler.add_job(check, 'interval', minutes=2, next_run_time=datetime.now())
    logger.info("Process started")
    scheduler.start()


def main():
    global auths, CAMPUS
    parser = ArgumentParser(description="HDU campus network login")
    parser.add_argument("--campus", choices=CAMPUS_CONFIGS, default="xiasha")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Check online status without logging in or out")
    modes.add_argument("--once", action="store_true", help="Run the login flow without logging out or scheduling jobs")
    args = parser.parse_args()
    CAMPUS = args.campus
    setup_logger()
    if args.check:
        result = Manager().check()
        return 0 if result.get("error") == "ok" else 1
    auths = load_auths()
    if args.once:
        result = Manager(*get_random_auth()).login()
        return 0 if result.get("suc_msg") else 1
    start_scheduler()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
