from enum import Enum
import re
import shlex
import subprocess
from enum import Enum
from system import run_and_check, CommandValidationException


PREFFERED_PROFILE = 'headset-head-unit'


class BluezAddressType(Enum):
    BR_EDR = 0
    LE_PUBLIC = 1
    LE_RANDOM = 2

    def __str__(self):
        return self.name


def is_valid_bluezaddress(address: str) -> bool:
    ok = True
    try:
        Address(address)
    except ValueError:
        ok = False

    return ok


class Address:
    regexp = re.compile(r"(?i:^([\da-f]{2}:){5}[\da-f]{2}$)")

    def __init__(self, value: str):
        if self.regexp.match(value) is None:
            raise ValueError(f"{value} is not a valid bluetooth address")
        self._address = value.lower()

    def __str__(self):
        return self._address

    def __eq__(self, other):
        return self._address == str(other).lower()


class BluezTarget:
    regexp = re.compile(r"(?i:^([\da-f]{2}:){5}[\da-f]{2}$)")

    def __init__(
        self, address: str, type: int | BluezAddressType = BluezAddressType.BR_EDR
    ):
        self.address = Address(address)
        if isinstance(type, int):
            type = BluezAddressType(type)
        elif isinstance(type, str):
            type = BluezAddressType(int(type))
        self.type = type

    def __eq__(self, other):
        return self.address == other.address and self.type == other.type


class BluezIoCaps(Enum):
    DisplayOnly = 0
    DisplayYesNo = 1
    KeyboardOnly = 2
    NoInputNoOutput = 3
    KeyboardDisplay = 4


def pair(target: BluezTarget, verbose: bool = False) -> bool:
    # Configure ourselves to be bondable and pairable
    run_and_check(shlex.split("sudo btmgmt bondable true"), verbose=verbose)
    run_and_check(shlex.split("sudo btmgmt pairable true"), verbose=verbose)

    # No need for link security ;)
    run_and_check(shlex.split("sudo btmgmt linksec false"), verbose=verbose)

    # Try to pair to a device with NoInputNoOutput capabilities
    # TODO: Sometimes this may fail due to agent requesting user confirmation.
    # Registering the following agent may help: "yes | bt-agent -c NoInputNoOutput"
    try:
        run_and_check(
            shlex.split(
                f"sudo btmgmt pair -c {str(BluezIoCaps.NoInputNoOutput.value)} -t {str(target.type.value)} {str(target.address)}"
            ),
            is_valid=lambda out: not ("failed" in out and not "Already Paired" in out),
            verbose=verbose,
        )
        return True
    except CommandValidationException as e:
        if "status 0x05 (Authentication Failed)" in e.output:
            return False
        raise e


def connect(target: BluezTarget, timeout: int = 2, verbose: bool = False):
    run_and_check(
        shlex.split(f"bluetoothctl --timeout {str(timeout)} scan on"), verbose=verbose
    )
    run_and_check(
        shlex.split(f"bluetoothctl connect {str(target.address)}"),
        is_valid=lambda out: not "Failed to connect" in out,
        verbose=verbose
    )


def normalize_address(target: BluezTarget) -> str:
    return str(target.address).upper().replace(":", "_")


def to_card_name(target: BluezTarget) -> str:
    return "bluez_card." + normalize_address(target=target)


def to_source_name(target: BluezTarget) -> str:
    return "bluez_input." + normalize_address(target=target) + ".0"


def get_bluetooth_profile(card_name: str, verbose: bool = False) -> str:
    result = subprocess.run(
        ['pactl', 'list', 'cards'],
        capture_output=True,
        text=True,
        check=False
    )

    if result.returncode != 0:
        raise CommandValidationException(
            "pactl list cards",
            result.stderr or "pactl command failed"
        )

    output = result.stdout

    card_section = []
    in_our_card = False

    for line in output.split('\n'):
        if f"Name: {card_name}" in line:
            in_our_card = True
            card_section = []
        elif in_our_card:
            if line.startswith('Card #') or (line.startswith('\t\tName:') and line.strip() != f"Name: {card_name}"):
                break
            card_section.append(line)

    if not card_section:
        raise CommandValidationException(
            f"pactl list cards (search for {card_name})",
            f"Card {card_name} not found in pactl output"
        )

    profiles = []
    in_profiles_section = False

    for line in card_section:
        if 'Profiles:' in line:
            in_profiles_section = True
            continue

        if in_profiles_section:
            if line.startswith('\t\t') and ':' in line:
                match = re.match(r'\t\t([^:]+):', line)
                if match:
                    profile_name = match.group(1).strip()
                    line_lower = line.lower()
                    is_available = 'available: yes' in line_lower or 'available: unknown' in line_lower
                    has_sources = 'sources: 0' not in line_lower
                    if is_available and has_sources:
                        profiles.append(profile_name)
                        if profile_name == PREFFERED_PROFILE:
                            if verbose:
                                print(f"Using preferred profile: {profile_name}")
                            return profile_name
            elif not line.startswith('\t\t'):
                break

    if not profiles:
        raise CommandValidationException(
            f"pactl list cards (get profiles for {card_name})",
            f"No available profiles found for card {card_name}"
        )

    if verbose:
        print(f"Using fallback profile: {profiles[0]} (available: {', '.join(profiles)})")

    return profiles[0]


def record(target: BluezTarget, outfile: str, verbose: bool = True):
    source_name = to_source_name(target)
    card_name = to_card_name(target)

    run_and_check(
        shlex.split(f"pactl set-card-profile {card_name} {get_bluetooth_profile(card_name, verbose)}"),
        verbose=verbose,
    )
    try:
        run_and_check(["parecord", "-d", source_name, outfile], verbose=verbose)
    except KeyboardInterrupt:
        pass
    except:
        raise


def playback(sink: str, file: str, verbose: bool = True):
    run_and_check(["paplay", "-d", sink, file], verbose=verbose)
