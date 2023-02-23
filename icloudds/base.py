#!/usr/bin/env python
"""Main script that uses Click to parse command-line arguments"""
from __future__ import print_function

import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta
import calendar
import time
import hashlib

import click
import urllib3
from urllib3.exceptions import InsecureRequestWarning
from watchdog.observers import Observer
from watchdog.events import PatternMatchingEventHandler
urllib3.disable_warnings(category=InsecureRequestWarning)

from pyicloud.exceptions import (PyiCloud2SARequiredException,
                                 PyiCloudAPIResponseException,
                                 PyiCloudFailedLoginException)

# Must import the constants object so that we can mock values in tests.
import icloudds.constants as constants
from icloudds.authentication import authenticate
from icloudds.email_notifications import send_2sa_notification
from icloudds.logger import setup_logger
from icloudds.logger import setup_database_logger
import icloudds.database as database

class iCloudDriveHandler(PatternMatchingEventHandler):
    def __init__(self, drive, directory, log_level):
        super().__init__(ignore_patterns={database.DatabaseHandler.db_base_name + "*", directory})
        self.drive = drive
        self.directory = directory
        self.log_level = log_level
        self.db = None

    def setup(self):
        if self.db == None:
            self.logger = setup_logger()

            self.logger.disabled = False
            if self.log_level == "debug":
                self.logger.setLevel(logging.DEBUG)
            elif self.log_level == "info":
                self.logger.setLevel(logging.INFO)
            elif self.log_level == "error":
                self.logger.setLevel(logging.ERROR)
            database.setup_database(self.directory)
            setup_database_logger()
            self.db = database.DatabaseHandler()

    def on_any_event(self, event):
        return
        self.setup()
        if event.is_directory:
            if event.src_path + "/" == self.directory:
                return
            self.logger.debug(f"ANY      {event.src_path} directory any event {event}")
        else:
            self.logger.debug(f"ANY      {event.src_path} file any event {event}")

    def on_created(self, event):
        self.setup()
        if event.is_directory:
            self.logger.debug(f"CREATED  {event.src_path} directory created {event}")
            paths = os.path.split(event.src_path)
            self.logger.debug(f"CREATED  {paths}")
            parent = self._get_icloud_node(paths[0])
            if parent is None:
                parent = self.drive.root
            self.logger.debug(f"CREATED  folder {paths[1]} in parent {parent.name}")
            self.drive.create_folders(parent.data['drivewsid'], paths[1])
            parent.reget_children()

        else:
            filename = os.path.split(event.src_path)[1]
            self.logger.debug(f"CREATED  {event.src_path} file created {event}")
            obj = self._get_icloud_node(event.src_path)
            parent = self._get_icloud_parent(event.src_path)
            count = 0
            if obj is not None:
                if self._need_to_upload(event.src_path, obj):
                    self.logger.debug(f"CREATED  {event.src_path} needs to be uploaded/_need_to_upload")
                    obj.delete()
                    count = self._upload_file(os.path.split(event.src_path)[0], filename, parent, "on_created")
            else:
                self.logger.debug(f"CREATED  {event.src_path} needs to be uploaded/created")
                count = self._upload_file(os.path.split(event.src_path)[0], filename, parent, "on_created")
            if count > 0 and parent is not None:
                parent.reget_children()

    def on_deleted(self, event):
        self.setup()
        if event.is_directory:
            self.logger.debug(f"DELETED  {event.src_path} directory deleted {event}")
            obj = self._get_icloud_node(event.src_path)
            parent = self._get_icloud_parent(event.src_path)
            if obj is not None and obj is not self.drive.root:
                obj.delete()
                self.logger.debug(f"DELETED  {obj.name}")
            elif obj == self.drive.root:
                self.logger.warn(f"DELETED on_delete not deleting root iCloud Drive folder!")
            if parent is not None:
                parent.reget_children()
        else:
            obj = self._get_icloud_node(event.src_path)
            if obj is not None:
                self.logger.debug(f"DELETED  {event.src_path} file needs to be deleted {event}")
                obj.delete()
            else:
                self.logger.debug(f"DELETED  {event.src_path} file does not need to be deleted {event}")
            self.db.delete_asset(event.src_path)
            parent = self._get_icloud_parent(event.src_path)
            if parent is not None:
                parent.reget_children()

    def on_modified(self, event):
        self.setup()
        if event.is_directory:
            self.logger.debug(f"MODIFIED {event.src_path} directory modified {event}")
        else:
            filename = os.path.split(event.src_path)[1]
            obj = self._get_icloud_node(event.src_path)
            parent = self._get_icloud_parent(event.src_path)
            count = 0
            if obj is not None:
                if self._need_to_upload(event.src_path, obj):
                    self.logger.debug(f"MODIFIED {event.src_path} needs to be uploaded/_need_to_upload")
                    obj.delete()
                    count = self._upload_file(os.path.split(event.src_path)[0], filename, parent, "on_modified")
                else:
                    self.logger.debug(f"MODIFIED {event.src_path} did not need to be uploaded")
            else:
                self.logger.debug(f"MODIFIED {event.src_path} needs to be uploaded/created")
                count = self._upload_file(os.path.split(event.src_path)[0], filename, parent, "on_modified")
            if count > 0 and parent is not None:
                parent.reget_children()

    def on_moved(self, event):
        self.setup()
        if event.is_directory:
            self.logger.debug(f"MOVED    {event.src_path} directory moved to {event.dest_path} {event}")
            obj = self._get_icloud_node(event.src_path)
            if obj is not None:
                src_parent_path = os.path.split(event.src_path)[0]
                src_parent = self._get_icloud_node(src_parent_path)
                if src_parent is None:
                    src_parent = self.drive.root

                dst_parent_path = os.path.split(event.dest_path)[0]
                dst_parent = self._get_icloud_node(dst_parent_path)
                if dst_parent is None:
                    dst_parent = self.drive.root

                if src_parent_path == dst_parent_path:
                    obj.rename(os.path.split(event.dest_path)[1])
                else:
                    if obj != self.drive.root:
                        obj.delete()
                    dst_parent.reget_children()
                src_parent.reget_children()

        else:
            filename = os.path.split(event.dest_path)[1]
            self.logger.debug(f"MOVED    {event.src_path} file moved to {event.dest_path} {event}")
            try:
                obj = self._get_icloud_node(event.dest_path)
            except KeyError as ex:
                # means dest_path folder(s) do not exist in iCloud
                self._create_icloud_folders(event.dest_path)
                obj = self._get_icloud_node(event.dest_path)

            count = 0
            parent = self._get_icloud_parent(event.dest_path)
            if obj is not None:
                if self._need_to_upload(event.dest_path, obj):
                    self.logger.debug(f"MOVED    {event.dest_path} file needs to be uploaded/_need_to_upload {event}")
                    obj.delete()
                    count = self._upload_file(os.path.split(event.dest_path)[0], filename, parent, "on_moved")
            else:
                self.logger.debug(f"MOVED    {event.dest_path} file needs to be uploaded/created {event}")
                count = self._upload_file(os.path.split(event.dest_path)[0], filename, parent, "on_moved")
            if count > 0 and parent is not None:
                parent.reget_children()

            try:
                obj = self._get_icloud_node(event.src_path)
                if obj is not None:
                    self.logger.debug(f"MOVED    {event.src_path} file needs to be deleted {event}")
                    obj.delete()
                parent = self._get_icloud_parent(event.src_path)
                if parent is not None:
                    parent.reget_children()
            except KeyError as ex:
                # folder moved, nothing needs to happen
                folder = self._get_deepest_folder(event.src_path)
                folder.reget_children()

    def on_closed(self, event):
        return
        self.setup()
        if event.is_directory:
            self.logger.debug(f"CLOSED   {event.src_path} directory closed {event}")
        else:
            self.logger.debug(f"CLOSED   {event.src_path} file closed {event}")

    def _upload_file(self, base, file, parent, reason):
        cwd = os.getcwd()
        os.chdir(base)
        try:
            size = os.path.getsize(file)
            filename, ext = os.path.splitext(file)
            mtime = os.path.getmtime(file)
            ctime = os.path.getctime(file)
            retries = 0
            while retries < constants.DOWNLOAD_MEDIA_MAX_RETRIES:
                try:
                    with open(file, 'rb') as f:
                        self.logger.info(f"uploading {reason} {os.path.join(base,file)}")
                        parent.upload(f, mtime=mtime, ctime=ctime)
                        md5 = self._calculate_md5(file)
                        self.logger.info(f"updating {os.path.join(base,file)} md5 {md5}")
                        self.db.upsert_asset(file, parent.name, size, ctime, mtime, ext, f"{os.path.join(base,file)}", md5)
                        break
                except PyiCloudAPIResponseException as ex:
                    retries = retries + 1
                    self.logger.info(f"exception in upload, retries = {retries}, retrying in {constants.DOWNLOAD_MEDIA_RETRY_CONNECTION_WAIT_SECONDS} seconds")
                    time.sleep(constants.DOWNLOAD_MEDIA_RETRY_CONNECTION_WAIT_SECONDS)

            os.chdir(cwd)
            if retries < constants.DOWNLOAD_MEDIA_MAX_RETRIES:
                return 1
            else:
                return 0
        except FileNotFoundError as ex:
            # caused by temporary files, e.g. when using rsync
            pass
        except Exception as ex:
            self.logger.warn(f"_upload_file exception {ex}")
        return 0

    def _create_icloud_folders(self, path):
        """Create the iCloud Drive folder objects (as needed) represented by path"""
        folder = self.drive.root
        icfs = self._get_icloud_folders(path)

        # Walk the path and try accessing the child object. If KeyError exception, the
        # child object does not exist, so create the folder and continue down the path
        for f in icfs:
            try:
                folder = folder[f]
            except KeyError as ex:
                self.logger.debug(f"_create_icloud_folders creating folder {f} in {folder.name}")
                self.drive.create_folders(folder.data['drivewsid'], f)
                folder.reget_children()

    def _get_deepest_folder(self, path):
        folder = self.drive.root
        icfs = self._get_icloud_folders(path)
        for f in icfs:
            try:
                folder = folder[f]
            except KeyError as ex:
                pass
        return folder

    def _get_icloud_parent(self, path):
        """Return the DriveNode object that is the parent of the DriveNode object represented by path"""
        folder = self.drive.root
        icfs = self._get_icloud_folders(path)

        for f in icfs:
            try:
                folder = folder[f]
            except KeyError as ex:
                return None
        return folder

    def _get_icloud_node(self, path):
        """Return the DriveNode object representing the given path, otherwise None"""
        folder = self.drive.root
        icfs = self._get_icloud_folders(path)
        filename = os.path.split(path)[1]

        # for each folder in the hierarchy, walk down to the leaf folder
        # if we get a KeyError exception the node does not exist (folder)
        for f in icfs:
            try:
                folder = folder[f]
            except KeyError as ex:  # folder does not exist in iCloud
                return None
            
        # folder is either self.drive.root, or the folder containing the item
        # if we get a KeyError exception the node does not exist (file)
        try:
            return folder[filename]
        except KeyError as ex:
            return None # file does not exist in iCloud
        

    def _get_icloud_folders(self, path):
        """Return a list of folder names representing the path deeper that the root folder"""
        _ = os.path.split(path)
        return [a for a in _[0].split(os.sep) if a not in self.directory.split(os.sep)]

    def _need_to_upload(self, path, obj):
        if obj.size is not None and os.path.getsize(path) != obj.size:
            self.logger.warn(f"size difference: {path} {os.path.getsize(path)} {obj.name} {obj.size}")

        mt = self._round_seconds(datetime.utcfromtimestamp(os.path.getmtime(path)))
        self._log_mtime_diff(mt, path, obj)

        if mt > obj.date_modified:
            return True

        if os.path.getsize(path) == 0 and obj.size is None:
            return False
        
        self.logger.info(f"skipping upload: {path}")
        return False

    def _need_to_download(self, path, obj):
        if obj.size is not None and os.path.getsize(path) != obj.size:
            self.logger.warn(f"size difference: {path} {os.path.getsize(path)} {obj.name} {obj.size}")

        mt = self._round_seconds(datetime.utcfromtimestamp(os.path.getmtime(path)))
        self._log_mtime_diff(mt, path, obj)

        if mt < obj.date_modified:
            return True

        if os.path.getsize(path) == 0 and obj.size is None:
            return False

        self.logger.info(f"skipping download: {path}")
        return False

    def _round_seconds(self, obj: datetime) -> datetime:
        """iCloud Drive stores files in the cloud using UTC, however it rounds the seconds up to the nearest second"""
        if obj.microsecond >= 500_000:
            obj += timedelta(seconds=1)
        return obj.replace(microsecond=0)

    def _log_mtime_diff(self, mt, path, obj):
        if mt < obj.date_modified:
            self.logger.debug(f"{path} size: {os.path.getsize(path)} {obj.size} date: {mt} < {obj.date_modified}")
        elif mt > obj.date_modified:
            self.logger.debug(f"{path} size: {os.path.getsize(path)} {obj.size} date: {mt} > {obj.date_modified}")
        else:
            self.logger.debug(f"{path} size: {os.path.getsize(path)} {obj.size} date: {mt} = {obj.date_modified}")

    def _calculate_md5(self, path):
        with open(path, 'rb') as f:
            data = f.read()    
        return hashlib.md5(data).hexdigest()

    def _recurse_icloud_drive(self, folder, directory):
        """Recurse the iCloud Drive folder structure, create local folders under directory
        as needed. Download files to the local folders, as needed. Set the mtime of downloaded
        files to the epoch seconds in UTC"""
        self.logger.info(f"recursing iCloud Drive folder {folder.name}")
        files_downloaded = 0
        children = folder.get_children()
        for child in children:
            if child.type != "file":
                path = os.path.join(directory, child.name)
                if not os.path.exists(path):
                    self.logger.info(f"creating local directory {path}")
                    os.makedirs(path)
                files_downloaded = files_downloaded + self._recurse_icloud_drive(child, path)
            else:
                path = os.path.join(directory, child.name)
                if not os.path.exists(path) or self._need_to_download(path, child):
                    self.logger.info(f"downloading {path}")
                    with open(path, 'wb') as f:
                        for chunk in child.open(stream=True).iter_content(chunk_size=constants.DOWNLOAD_MEDIA_CHUNK_SIZE):
                            if chunk:
                                f.write(chunk)
                                f.flush()
                        # file timestamps from DriveService are represented in UTC and rounded to nearest second
                        dt = child.date_modified
                        epoch_seconds = calendar.timegm(dt.timetuple())
                        os.utime(path, (epoch_seconds, epoch_seconds))
                        md5 = self._calculate_md5(path)
                        self.db.upsert_asset(child.name, folder.name, child.size, child.date_changed, child.date_modified, child.type, path, md5)
                        files_downloaded + files_downloaded + 1
        return files_downloaded

    def _walk_local_drive(self):
        """Walk the local filesystem, create folders in iCloud corresponding the folders below
        self.directory as needed. Upload files found to iCloud if the modified time is newer than
        the DriveNode or if the DriveNode was not found"""
        files_uploaded = 0
        dir_base = self.directory.split(os.sep)
        for base, dirs, files in os.walk(self.directory):
            path = base.split(os.sep)
            path = [a for a in path if a not in dir_base]
            if (len(path) == 0):
                parent = self.drive.root
            else:
                parent = self.drive.root
                for i in range(len(path)):
                    parent_name = path[i]
                    parent = parent[parent_name]

            reget_children = False
            for dir in dirs:
                try:
                    folder = parent[dir]
                except KeyError:
                    self.logger.info(f"creating folder {parent.data.get('parentId', 'FOLDER::com.apple.CloudDocs::root')}::{parent.name}/{dir} in iCloud")
                    self.drive.create_folders(parent.data['drivewsid'], dir)
                    reget_children = True
            
            if reget_children:
                parent.reget_children()

            for f in files:
                try:
                    node = parent[f]
                    if self._need_to_upload(os.path.join(base, f), parent[f]):
                        node.delete()
                        files_uploaded = files_uploaded + self._upload_file(base, f, parent, "_walk_local_drive/local is newer")

                except KeyError:
                    self.logger.debug(f"{os.path.join(base, f)} does not exist in iCloud")
                    _, filename = os.path.split(database.DatabaseHandler.db_file)
                    if not filename == f:
                       files_uploaded = files_uploaded + self._upload_file(base, f, parent,  "_walk_local_drive/new file")
        return files_uploaded

    def sync_iCloudDrive(self, sync):
        self.setup()

        start = datetime.now()
        if sync == True:
            self.logger.info(f"syncing iCloud Drive to {self.directory} ...")
            downloaded = self._recurse_icloud_drive(self.drive.root, self.directory)
            self.logger.info(f"syncing {self.directory} to iCloud Drive ...")
            uploaded = self._walk_local_drive()
            self.logger.info(f"{downloaded} files downloaded from iCloud Drive")
            self.logger.info(f"{uploaded} files uploaded to iCloud Drive")
            self.logger.info(f"completed in {datetime.now() - start}")
        self.db.db_conn.close()
        self.db = None


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

@click.command(context_settings=CONTEXT_SETTINGS, options_metavar="<options>")
# @click.argument(
@click.option("-d", "--directory",     help="Local directory that should be used for download", type=click.Path(exists=True), metavar="<directory>")
@click.option("-u", "--username",      help="Your iCloud username or email address", metavar="<username>")
@click.option("-p", "--password",      help="Your iCloud password (default: use PyiCloud keyring or prompt for password)", metavar="<password>")
@click.option("--cookie-directory",    help="Directory to store cookies for authentication (default: ~/.pyicloud)", metavar="</cookie/directory>", default="~/.pyicloud")
@click.option("--sync",                help="Runs an initial scan of iCloud Drive and local filesystem", is_flag=True, default=False)
@click.option("--smtp-username",       help="Your SMTP username, for sending email notifications when two-step authentication expires.", metavar="<smtp_username>")
@click.option("--smtp-password",       help="Your SMTP password, for sending email notifications when two-step authentication expires.", metavar="<smtp_password>")
@click.option("--smtp-host",           help="Your SMTP server host. Defaults to: smtp.gmail.com", metavar="<smtp_host>", default="smtp.gmail.com")
@click.option("--smtp-port",           help="Your SMTP server port. Default: 587 (Gmail)", metavar="<smtp_port>", type=click.IntRange(0), default=587)
@click.option("--smtp-no-tls",         help="Pass this flag to disable TLS for SMTP (TLS is required for Gmail)", metavar="<smtp_no_tls>", is_flag=True)
@click.option("--notification-email",  help="Email address where you would like to receive email notifications. Default: SMTP username", metavar="<notification_email>")
@click.option("--notification-script", help="Runs an external script when two factor authentication expires. (path required: /path/to/my/script.sh)", type=click.Path(), )
@click.option("--log-level",           help="Log level (default: debug)", type=click.Choice(["debug", "info", "error"]), default="debug")
@click.option("--unverified-https",    help="Overrides default https context with unverified https context", is_flag=True)

@click.version_option()
# pylint: disable-msg=too-many-arguments,too-many-statements
# pylint: disable-msg=too-many-branches,too-many-locals

def main(
        directory,
        username,
        password,
        cookie_directory,
        sync,
        smtp_username,
        smtp_password,
        smtp_host,
        smtp_port,
        smtp_no_tls,
        notification_email,
        notification_script,         # pylint: disable=W0613
        log_level,
        unverified_https,
):
    """Synchronize local folder with iCloud Drive and watch for file system changes"""
    logger = setup_logger()

    logger.disabled = False
    if log_level == "debug":
        logger.setLevel(logging.DEBUG)
    elif log_level == "info":
        logger.setLevel(logging.INFO)
    elif log_level == "error":
        logger.setLevel(logging.ERROR)

    directory = os.path.abspath(directory)

    logger.info(f"directory: {directory}")
    logger.info(f"username: {username}")
    logger.info(f"cookie_directory: {cookie_directory}")
    logger.info(f"smtp_username: {smtp_username}")
    logger.info(f"smtp_password: {smtp_password}")
    logger.info(f"smtp_host: {smtp_host}")
    logger.info(f"smtp_port: {smtp_port}")
    logger.info(f"smtp_no_tls: {smtp_no_tls}")
    logger.info(f"notification_email: {notification_email}")
    logger.info(f"log_level: {log_level}")
    logger.info(f"notification_script: {notification_script}")
    logger.info(f"unverified_https: {unverified_https}")
        
    # check required directory param only if not list albums
    if not directory:
        print('--directory is required')
        sys.exit(constants.ExitCode.EXIT_FAILED_MISSING_COMMAND.value)

    database.setup_database(directory)
    setup_database_logger()
    
    raise_authorization_exception = (
        smtp_username is not None
        or notification_email is not None
        or notification_script is not None
        or not sys.stdout.isatty()
    )

    try:
        logger.debug("connecting to iCloudService...")
        icloud = authenticate(username, password, cookie_directory, raise_authorization_exception, client_id=os.environ.get("CLIENT_ID"), unverified_https=unverified_https)
    except PyiCloud2SARequiredException as ex:
        if notification_script is not None:
            logger.debug(f"executing notification script {notification_script}")
            subprocess.call([notification_script])
        if smtp_username is not None or notification_email is not None:
            logger.debug(f"sending 2sa email notification")
            send_2sa_notification(smtp_username, smtp_password, smtp_host, smtp_port, smtp_no_tls, notification_email)
        logger.error(ex)
        sys.exit(constants.ExitCode.EXIT_FAILED_2FA_REQUIRED.value)
    except PyiCloudFailedLoginException as ex:
        logger.error(ex)
        sys.exit(constants.ExitCode.EXIT_FAILED_LOGIN.value)


    try:
        handler = iCloudDriveHandler(icloud.drive, directory, log_level)
        handler.sync_iCloudDrive(sync)
        logger.info("watching for filesystem change events...")
        observer = Observer()
        observer.schedule(handler, path=directory, recursive=True)
        observer.start()
        try:
            while True:
                time.sleep(1)
        finally:
            observer.stop()
            observer.join()

    except PyiCloudAPIResponseException as ex:
        # For later: come up with a nicer message to the user. For now take the
        # exception text
        print(ex)
        sys.exit(constants.ExitCode.EXIT_FAILED_CLOUD_API.value)

