"""Handles username/password authentication and two-step authentication"""

import sys
import click
import logging

import pyicloud
from icloudds.logger import setup_logger
import icloudds.constants as constants
import pyicloud.utils as utils
from pyicloud.exceptions import PyiCloud2SARequiredException
from pyicloud.exceptions import PyiCloudFailedLoginException
from pyicloud.exceptions import PyiCloudNoStoredPasswordAvailableException

def authenticate(
    username,
    password,
    cookie_directory=None,
    raise_authorization_exception=False,
    client_id=None,
    unverified_https=False
):
    """Authenticate with iCloud username and password"""
    logger = setup_logger()
    logger.debug("Authenticating...")

    failure_count = 0
    while True:
        try:
            logger.debug(f"username: {username}")
            logger.debug(f"password: {password is not None}")
            logger.debug(f"cookie_directory: {cookie_directory}")
            logger.debug(f"client_id: {client_id}")
            logger.debug(f"raise_authorization_exception: {raise_authorization_exception}")
            logger.debug(f"unverified_https: {unverified_https}")
            api = pyicloud.PyiCloudService(
                username,
                password,
                cookie_directory=cookie_directory,
                client_id=client_id,
                verify=not unverified_https)
                
            if api.requires_2fa:
                # fmt: off
                # fmt: on
                if raise_authorization_exception:
                    raise PyiCloud2SARequiredException(username)
#---
                security_key_names = api.security_key_names
                if security_key_names:
                    print(
                        f"Security key confirmation is required. "
                        f"Please plug in one of the following keys: {', '.join(security_key_names)}"
                    )
                    devices = api.fido2_devices
                    print("Available FIDO2 devices:")
                    for idx, dev in enumerate(devices, start=1):
                        print(f"{idx}: {dev}")

                    choice = click.prompt(
                        "Select a FIDO2 device by number",
                        type=click.IntRange(1, len(devices)),
                        default=1,
                    )
                    selected_device = devices[choice - 1]
                    print("Please confirm the action using the security key")
                    api.confirm_security_key(selected_device)
#---
                else:
                    print("\nTwo-factor (2FA) authentication required.")
                    code = input("\nPlease enter verification code: ")
                    result = api.validate_2fa_code(code)
                    print(f"Code validation result: {result}")
                    if not result:
                        logger.debug(f"Failed to verify (2FA) verification code {result}")
                        sys.exit(constants.ExitCode.EXIT_FAILED_VERIFY_2FA_CODE.value)

                if not api.is_trusted_session:
                    print("Session is not trusted. Requesting trust...")
                    result = api.trust_session()
                    print("Session trust result %s" % result)

                    if not result:
                        print("Failed to request trust. You will likely be prompted for confirmation again in the coming weeks")
                    
            elif api.requires_2sa:
                # fmt: off
                print("\nTwo-step (2SA) authentication required.")
                # fmt: on
                if raise_authorization_exception:
                    raise PyiCloud2SARequiredException(username)

                print("\nYour trusted devices are:")
                devices = api.trusted_devices
                for i, device in enumerate(devices):
                    print(
                        "    %s: %s"
                        % (
                            i,
                            device.get(
                                "deviceName", "SMS to %s" % device.get("phoneNumber")
                            ),
                        )
                    )

                device = click.prompt('Which device would you like to use?', default=0)
                device = devices[device]
                if not api.send_verification_code(device):
                    logger.debug("Failed to send verification code")
                    sys.exit(constants.ExitCode.EXIT_FAILED_SEND_2SA_CODE)

                code = input("\nPlease enter two-step (2SA) validation code: ")
                if not api.validate_verification_code(device, code):
                    print("Failed to verify verification code")
                    sys.exit(constants.ExitCode.EXIT_FAILED_VERIFY_2FA_CODE)
            # Auth success
            logger.info(f"Authenticated as {username}")
            return api

        except PyiCloudFailedLoginException as err:
            # If the user has a stored password; we just used it and
            # it did not work; let's delete it if there is one.
            if utils.password_exists_in_keyring(username):
                utils.delete_password_in_keyring(username)

            message = "Bad username or password for {username}".format(username=username)
            failure_count += 1
            if failure_count >= constants.AUTHENTICATION_MAX_RETRIES:
                raise PyiCloudFailedLoginException(message)

            logger.info(message)

        except PyiCloudNoStoredPasswordAvailableException:
            if raise_authorization_exception:
                message = f"No stored password available for {username} and not a TTY!"
                raise PyiCloudFailedLoginException(message)

            # Prompt for password if not stored in PyiCloud's keyring
            password = click.prompt("iCloud Password", hide_input=True)
            if (
                not utils.password_exists_in_keyring(username)
                and click.confirm("Save password in keyring?")
            ):
                utils.store_password_in_keyring(username, password)

def old_authenticate(
        username,
        password,
        cookie_directory=None,
        raise_error_on_2sa=False,
        client_id=None
):
    """Authenticate with iCloud username and password"""
    logger = setup_logger()
    logger.debug("Authenticating...")
    try:
        # If password not provided on command line variable will be set to None
        # and PyiCloud will attempt to retrieve from it's keyring
        icloud = pyicloud.PyiCloudService(
            username, password,
            cookie_directory=cookie_directory,
            client_id=client_id)
    except pyicloud.exceptions.PyiCloudNoStoredPasswordAvailableException:
        # Prompt for password if not stored in PyiCloud's keyring
        password = click.prompt("iCloud Password", hide_input=True)
        icloud = pyicloud.PyiCloudService(
            username, password,
            cookie_directory=cookie_directory,
            client_id=client_id)

    if icloud.requires_2sa:
        if raise_error_on_2sa:
            raise PyiCloud2SARequiredException(
                "Two-step/two-factor authentication is required!"
            )
        logger.info("Two-step/two-factor authentication is required!")
        request_2sa(icloud, logger)
    return icloud


def request_2sa(icloud, logger):
    """Request two-step authentication. Prompts for SMS or device"""
    devices = icloud.trusted_devices
    devices_count = len(devices)
    logger.debug(f"request_2sa() devices_count: {devices_count}, devices: {devices}")
    device_index = 0
    if devices_count > 0:
        for i, device in enumerate(devices):
            print(
                "  %s: %s" %
                (i, device.get(
                    "deviceName", "SMS to %s" %
                    device.get("phoneNumber"))))

        # pylint: disable-msg=superfluous-parens
        print("  %s: Enter two-factor authentication code" % devices_count)
        # pylint: enable-msg=superfluous-parens
        device_index = click.prompt(
            "Please choose an option:",
            default=0,
            type=click.IntRange(
                0,
                devices_count))

    if device_index == devices_count:
        # We're using the 2FA code that was automatically sent to the user's device,
        # so can just use an empty dict()
        device = dict()
    else:
        device = devices[device_index]
        if not icloud.send_verification_code(device):
            logger.error("Failed to send two-factor authentication code")
            sys.exit(constants.ExitCode.EXIT_FAILED_SEND_2FA_CODE.value)

    code = click.prompt("Please enter two-factor authentication code")
    if not icloud.validate_verification_code(device, code):
        logger.error("Failed to verify two-factor authentication code")
        sys.exit(constants.ExitCode.EXIT_FAILED_VERIFY_2FA_CODE.value)
    logger.info(
        "Great, you're all set up. The script can now be run without "
        "user interaction until 2SA expires.\n"
        "You can set up email notifications for when "
        "the two-step authentication expires.\n"
        "(Use --help to view information about SMTP options.)"
    )
