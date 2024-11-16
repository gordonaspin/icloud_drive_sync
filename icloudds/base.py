#!/usr/bin/env python
# pylint: skip-file
# """Main script that uses Click to parse command-line arguments"""
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
    def __init__(self, drive, directory, pseudoroot, log_level):
        super().__init__(ignore_patterns={database.DatabaseHandler.db_base_name + "*", directory})
        self.drive = drive
        self.pseudoroot = drive[pseudoroot] if pseudoroot is not None else drive.root
        self.directory = directory
        self.log_level = log_level
        self.db = None
        self.is_dirty = False

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
        self.is_dirty = True
        return
        self.setup()
        if event.is_directory:
            if event.src_path + "/" == self.directory:
                return
            self.logger.debug(f"{event.event_type}: {event.src_path} directory any event")
        else:
            self.logger.debug(f"{event.event_type}: {event.src_path} file any event")

    def on_created(self, event):
        self.setup()
        if event.is_directory:
            self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} directory created")
            paths = os.path.split(event.src_path)
            parent = self._get_icloud_node(paths[0])
            if parent is None:
                parent = self.pseudoroot
            self.logger.info(f"creating folder {self._get_node_path_as_str(event.src_path, paths[1])}")
            #self.logger.debug(f"CREATED  folder {paths[1]} in parent {parent.name}")
            parent.mkdir(paths[1])
            #self.drive.create_folders(parent.data['drivewsid'], paths[1])
            self.logger.debug(f"getting children of {parent.name} in on_created / directory")
            parent.get_children(True)

        else:
            filename = os.path.split(event.src_path)[1]
            self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} file created")
            obj = self._get_icloud_node(event.src_path)
            parent = self._get_icloud_parent(event.src_path)
            count = 0
            if obj is not None:
                if self._need_to_upload(event.src_path, obj):
                    self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} needs to be uploaded/_need_to_upload")
                    retcode = obj.delete()
                    self.logger.debug(f"on_created, delete {obj.name} returned {retcode}")
                    count = self._upload_file(os.path.split(event.src_path)[0], filename, parent, "on_created")
                    if count:
                        self.logger.debug(f"getting children of {parent.name} in on_created _need_to_upload")
                        parent.get_children(True)
                    self._update_md5(event.src_path)
            else:
                self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} needs to be uploaded/created")
                count = self._upload_file(os.path.split(event.src_path)[0], filename, parent, "on_created")
                if count:
                    self.logger.debug(f"getting children of {parent.name} in on_created")
                    parent.get_children(True)
                self._update_md5(event.src_path)

        return

    def on_deleted(self, event):
        self.setup()
        if event.is_directory:
            self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} directory deleted")
            obj = self._get_icloud_node(event.src_path)
            parent = self._get_icloud_parent(event.src_path)
            if obj is not None and obj is not self.pseudoroot:
                self.logger.info(f"{event.event_type}: deleting folder {self._get_node_path_as_str(event.src_path, obj.name)}")
                retcode = obj.delete()
                self.logger.debug(f"on_deleted (directory), delete {obj.name} returned {retcode}")
            elif obj == self.pseudoroot:
                self.logger.warn(f"{event.event_type}: on_delete not deleting root iCloud Drive folder!")
            if parent is not None:
                self.logger.debug(f"getting children of {parent.name} in on_deleted / directory")
                parent.remove(obj)
                #parent.get_children(True)
        else:
            self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} file deleted")
            obj = self._get_icloud_node(event.src_path)
            if obj is not None:
                self.logger.info(f"{event.event_type}: {event.src_path[len(self.directory)+1:]}")
                retcode = obj.delete()
                self.logger.debug(f"on_deleted (file), delete {obj.name} returned {retcode}")
            else:
                self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} file does not need to be deleted")
            self.db.delete_asset(event.src_path)
            parent = self._get_icloud_parent(event.src_path)
            if parent is not None and obj is not None:
                self.logger.debug(f"getting children of {parent.name} in on_deleted / file")
                parent.remove(obj)
                #parent.get_children(True)

        return
    
    def on_modified(self, event):
        self.setup()
        if event.is_directory:
            self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} directory modified")
        else:
            self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} file modified")
            filename = os.path.split(event.src_path)[1]
            obj = self._get_icloud_node(event.src_path)
            parent = self._get_icloud_parent(event.src_path)
            count = 0
            if obj is not None:
                if self._need_to_upload(event.src_path, obj):
                    self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} needs to be uploaded/_need_to_upload")
                    retcode = obj.delete()
                    self.logger.debug(f"on_modified (file), delete {obj.name} returned {retcode}")
                    count = self._upload_file(os.path.split(event.src_path)[0], filename, parent, "on_modified")
                    if count:
                        self.logger.debug(f"getting children of {parent.name} in on_modified _need_to_upload")
                        parent.get_children(True)
                    self._update_md5(event.src_path)
                else:
                    self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} did not need to be uploaded")
            else:
                self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} needs to be uploaded/created")
                count = self._upload_file(os.path.split(event.src_path)[0], filename, parent, "on_modified")
                if count:
                    self.logger.debug(f"getting children of {parent.name} in on_modified")
                    parent.get_children(True)
                self._update_md5(event.src_path)

    def on_moved(self, event):
        self.setup()
        if event.is_directory:
            self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} directory moved to {event.dest_path[len(self.directory)+1:]}")
            obj = self._get_icloud_node(event._src_path)
            if obj is not None:
                src_parent_path = os.path.split(event._src_path)[0]
                src_parent = self._get_icloud_node(src_parent_path)
                if src_parent is None:
                    src_parent = self.pseudoroot

                dst_parent_path = os.path.split(event._dest_path)[0]
                dst_parent = self._get_icloud_node(dst_parent_path)
                if dst_parent is None:
                    dst_parent = self.pseudoroot

                if src_parent_path == dst_parent_path:
                    name = os.path.split(event.dest_path)[1]
                    self.logger.debug(f"on_moved (directory), renaming {obj.name} in {src_parent.name} to {name}")
                    obj.rename(name)
                else:
                    if obj != self.pseudoroot:
                        retcode = obj.delete()
                        self.logger.debug(f"on_moved (directory), delete folder {obj.name} in {src_parent.name} returned {retcode}")
                    retcode = dst_parent.mkdir(obj.name)
                    self.logger.debug(f"on_moved (directory), create folder {obj.name} in {dst_parent.name} returned {retcode}")
                    self.logger.debug(f"getting children of {dst_parent.name} in on_moved / destination directory")
                    #dst_parent.get_children(True)
                    self.logger.debug(f"removing {obj.name} child of {src_parent.name} in on_moved / source directory")
                    src_parent.remove(obj)
                    #self._walk_local_drive(self._get_icloud_node(event.dest_path), event.dest_path)
                src_parent.get_children(True)

        else:
            filename = os.path.split(event.dest_path)[1]
            self.logger.debug(f"{event.event_type}: {event.src_path} file moved to {event.dest_path[len(self.directory)+1:]}")
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
                    self.logger.debug(f"{event.event_type}: {event.dest_path[len(self.directory)+1:]} file needs to be uploaded/_need_to_upload")
                    retcode = obj.delete()
                    self.logger.debug(f"on_moved (file destination), delete {obj.name} returned {retcode}")
                    count = self._upload_file(os.path.split(event.dest_path)[0], filename, parent, "on_moved")
                    if count:
                        self.logger.debug(f"getting children of {parent.name} in on_moved _need_to_upload")
                        parent.get_children(True)

                    self._update_md5(event.dest_path)
            else:
                self.logger.debug(f"{event.event_type}: {event.dest_path[len(self.directory)+1:]} file needs to be uploaded/created")
                count = self._upload_file(os.path.split(event.dest_path)[0], filename, parent, "on_moved")
                if count:
                    self.logger.debug(f"getting children of {parent.name} in on_moved")
                    parent.get_children(True)

                self._update_md5(event.dest_path)

            try:
                obj = self._get_icloud_node(event.src_path)
                if obj is not None:
                    self.logger.debug(f"{event.event_type}: {event.src_path[len(self.directory)+1:]} file needs to be deleted")
                    retcode = obj.delete()
                    self.logger.debug(f"on_moved (file source), delete {obj.name} returned {retcode}")

                    self.db.delete_asset(event.src_path)
                parent = self._get_icloud_parent(event.src_path)
                if parent is not None:
                    self.logger.debug(f"getting children of {parent.name} in on_moved / source file")
                    parent.remove(obj)
                    #parent.get_children(True)
            except KeyError as ex:
                # folder moved, nothing needs to happen
                parent = self._get_deepest_folder(event.src_path)
                self.logger.debug(f"getting children of {parent.name} in on_moved / source file exception")
                parent.get_children(True)

    def on_closed(self, event):
        return
        self.setup()
        if event.is_directory:
            self.logger.debug(f"{event.event_type}: {event.src_path} directory closed")
        else:
            self.logger.debug(f"{event.event_type}: {event.src_path} file closed")

    def _upload_file(self, base, file, parent, reason):
        try:
            cwd = os.getcwd()
            os.chdir(base)
            size = os.path.getsize(file)
            filename, ext = os.path.splitext(file)
            mtime = os.path.getmtime(file)
            ctime = os.path.getctime(file)
            retries = 0
            while retries < constants.DOWNLOAD_MEDIA_MAX_RETRIES:
                try:
                    with open(file, 'rb') as f:
                        self.logger.info(f"uploading {os.path.join(base,file)[len(self.directory)+1:]} {reason}")
                        parent.upload(f, mtime=mtime, ctime=ctime)
                        #self.logger.debug(f"getting children of {parent.name} in _upload_file")
                        #parent.get_children(True)
                        break
                except PyiCloudAPIResponseException as ex:
                    retries = retries + 1
                    self.logger.info(f"exception in upload, retries = {retries}, retrying in {constants.DOWNLOAD_MEDIA_RETRY_CONNECTION_WAIT_SECONDS} seconds")
                    time.sleep(constants.DOWNLOAD_MEDIA_RETRY_CONNECTION_WAIT_SECONDS)

            os.chdir(cwd)
            if retries < constants.DOWNLOAD_MEDIA_MAX_RETRIES:
                self.logger.debug(f"upload of {os.path.join(base,file)[len(self.directory)+1:]} {reason} returning 1")
                return 1
            else:
                self.logger.debug(f"upload of {os.path.join(base,file)[len(self.directory)+1:]} {reason} returning 0")
                return 0
        except FileNotFoundError as ex:
            self.logger.debug(f"FileNotFound exception in upload of {os.path.join(base,file)[len(self.directory)+1:]} {reason}")
            pass
        except Exception as ex:
            self.logger.warn(f"_upload_file exception {ex}")
        self.logger.debug(f"failed upload of {os.path.join(base,file)[len(self.directory)+1:]} {reason} returning 0")
        return 0

    def _create_icloud_folders(self, path):
        """Create the iCloud Drive folder objects (as needed) represented by path"""
        folder = self.pseudoroot
        icfs = self._get_icloud_folders(path)

        # Walk the path and try accessing the child object. If KeyError exception, the
        # child object does not exist, so create the folder and continue down the path
        for f in icfs:
            try:
                folder = folder[f]
            except KeyError as ex:
                self.logger.debug(f"_create_icloud_folders creating folder {f} in {folder.name}")
                folder.mkdir(f)
                self.logger.debug(f"getting children of {folder.name} in _create_icloud_folders")
                folder.get_children(True)

    def _get_deepest_folder(self, path):
        folder = self.pseudoroot
        icfs = self._get_icloud_folders(path)
        for f in icfs:
            try:
                folder = folder[f]
            except KeyError as ex:
                pass
        return folder

    def _get_icloud_parent(self, path):
        """Return the DriveNode object that is the parent of the DriveNode object represented by path"""
        folder = self.pseudoroot
        icfs = self._get_icloud_folders(path)

        for f in icfs:
            try:
                folder = folder[f]
            except KeyError as ex:
                return None
        return folder

    def _get_icloud_node(self, path):
        """Return the DriveNode object representing the given path, otherwise None"""
        folder = self.pseudoroot
        icfs = self._get_icloud_folders(path)
        filename = os.path.split(path)[1]

        # for each folder in the hierarchy, walk down to the leaf folder
        # if we get a KeyError exception the node does not exist (folder)
        for f in icfs:
            try:
                folder = folder[f]
            except KeyError as ex:  # folder does not exist in iCloud
                return None

        # folder is either self.pseudoroot, or the folder containing the item
        # if we get a KeyError exception the node does not exist (file)
        try:
            return folder[filename]
        except KeyError as ex:
            return None # file does not exist in iCloud

    def _get_icloud_folders(self, path):
        """Return a list of folder names representing the path deeper than the root folder"""
        _ = os.path.split(path)
        return [a for a in _[0].split(os.sep) if a not in self.directory.split(os.sep)]

    def _get_node_path_as_str(self, path, s):
        icfs = self._get_icloud_folders(path)
        icfs.append(s)
        return "/".join(icfs)

    def _get_folder_path_as_str(self, path, s):
        path = path[len(self.directory):]
        path = path + os.sep
        path = path + s
        return path[1:]

    def _need_to_upload(self, path, obj):
        try:
            if obj.size is not None and os.path.getsize(path) != obj.size:
                self.logger.warn(f"size difference: {path[len(self.directory)+1:]} {os.path.getsize(path)} {obj.name} {obj.size}")

            mt = self._round_seconds(datetime.utcfromtimestamp(os.path.getmtime(path)))
            self._log_mtime_diff(mt, path, obj)

            if mt > obj.date_modified:
                return True

            if os.path.getsize(path) == 0 and obj.size is None:
                return False

            self.logger.debug(f"skipping upload: {path[len(self.directory)+1:]}")
        except FileNotFoundError as ex:
            self.logger.debug("caught file not found exception in _need_to_upload()")

        return False

    def _need_to_download(self, path, obj):
        if obj.size is not None and os.path.getsize(path) != obj.size:
            self.logger.warn(f"size difference: {path[len(self.directory)+1:]} {os.path.getsize(path)} {obj.name} {obj.size}")

        mt = self._round_seconds(datetime.utcfromtimestamp(os.path.getmtime(path)))
        self._log_mtime_diff(mt, path, obj)

        if mt < obj.date_modified:
            return True

        if os.path.getsize(path) == 0 and obj.size is None:
            return False

        self.logger.debug(f"skipping download: {path[len(self.directory)+1:]}")
        return False

    def _round_seconds(self, obj: datetime) -> datetime:
        """iCloud Drive stores files in the cloud using UTC, however it rounds the seconds up to the nearest second"""
        if obj.microsecond >= 500_000:
            obj += timedelta(seconds=1)
        return obj.replace(microsecond=0)

    def _log_mtime_diff(self, mt, path, obj):
        if mt < obj.date_modified:
            self.logger.debug(f"{path[len(self.directory)+1:]} size: {os.path.getsize(path)} {obj.size} date: {mt} < {obj.date_modified}")
        elif mt > obj.date_modified:
            self.logger.debug(f"{path[len(self.directory)+1:]} size: {os.path.getsize(path)} {obj.size} date: {mt} > {obj.date_modified}")
        else:
            self.logger.debug(f"{path[len(self.directory)+1:]} size: {os.path.getsize(path)} {obj.size} date: {mt} = {obj.date_modified}")

    def _update_md5(self, path):
        try:
            with open(path, 'rb') as f:
                data = f.read()
            md5 = hashlib.md5(data).hexdigest()
            size = os.path.getsize(path)
            ctime = os.path.getctime(path)
            mtime = os.path.getmtime(path)
            _, ext = os.path.splitext(path)
            filename = os.path.split(path)[1]
            parent = self._get_icloud_parent(path)
            self.logger.debug(f"updating {path[len(self.directory)+1:]} md5 {md5}")
            self.db.upsert_asset(filename, parent.name, size, ctime, mtime, ext, path[len(self.directory)+1:], md5)
        except FileNotFoundError as ex:
            self.logger.debug("caught file not found exception in _need_to_upload()")



    def _recurse_icloud_drive(self, folder, directory):
        """Recurse the iCloud Drive folder structure, create local folders under directory
        as needed. Download files to the local folders, as needed. Set the mtime of downloaded
        files to the epoch seconds in UTC"""
        files_downloaded = 0
        folders_created = 0
        children = folder.get_children()
        for child in children:
            if child.type != "file":
                if child.name.startswith('.com-apple-bird'):
                    self.logger.info(f"skipping {child.name} iCloud Drive folder")
                else:
                    self.logger.info(f"recursing iCloud Drive folder {self._get_folder_path_as_str(directory, child.name)}")
                    path = os.path.join(directory, child.name)
                    if not os.path.exists(path):
                        self.logger.info(f"creating local directory {path[len(self.directory)+1:]}")
                        os.makedirs(path)
                        folders_created = folders_created + 1
                    folders_files = self._recurse_icloud_drive(child, path) #, files_downloaded, folders_created)
                    folders_created = folders_created + folders_files[0]
                    files_downloaded = files_downloaded + folders_files[1]
            else:
                path = os.path.join(directory, child.name)
                if not os.path.exists(path) or self._need_to_download(path, child):
                    self.logger.info(f"downloading {path[len(self.directory)+1:]}")
                    try:
                        with open(path, 'wb') as f:
                            for chunk in child.open(stream=True).iter_content(chunk_size=constants.DOWNLOAD_MEDIA_CHUNK_SIZE):
                                if chunk:
                                    f.write(chunk)
                                    f.flush()
                        # file timestamps from DriveService are in UTC and rounded to nearest second by Apple (◔_◔)
                            dt = child.date_modified
                            epoch_seconds = calendar.timegm(dt.timetuple())
                            os.utime(path, (epoch_seconds, epoch_seconds))
                            files_downloaded = files_downloaded + 1
                    except PermissionError:
                        self.logger.info(f"PermissionError downloading {path[len(self.directory)+1:]}")
                    
                    self._update_md5(path)
        self.logger.debug(f"_recurse_icloud_drive created {folders_created} folders and downloaded {files_downloaded} files")
        return (folders_created, files_downloaded)

    def _walk_local_drive(self, root_node, base_path):
        """Walk the local filesystem, create folders in iCloud corresponding the folders below
        base_path as needed. Upload files found to iCloud if the modified time is newer than
        the DriveNode or if the DriveNode was not found"""
        total_dirs_created = 0
        total_files_created = 0
        dir_base = base_path.split(os.sep)
        for base, dirs, files in os.walk(base_path):
            path = base.split(os.sep)
            path = [a for a in path if a not in dir_base]
            if (len(path) == 0):
                parent = root_node
            else:
                parent = root_node
                for i in range(len(path)):
                    parent_name = path[i]
                    parent = parent[parent_name]

            dirs_subtotal = 0
            for dir in dirs:
                try:
                    folder = parent[dir]
                except KeyError:
                    dirs_subtotal = dirs_subtotal + 1
                    self.logger.info(f"creating folder {parent.data.get('parentId', 'FOLDER::com.apple.CloudDocs::root')}::{parent.name}/{dir} in iCloud")
                    parent.mkdir(dir)

            total_dirs_created = total_dirs_created + dirs_subtotal
            # we made 1 or more new folders inside parent, so refresh parent's children nodes
            if dirs_subtotal:
                self.logger.debug(f"getting children of {parent.name} in _walk_local_drive")
                parent.get_children(True)

            self.logger.info(f"recursing local folder {os.path.relpath(base, base_path)}")
            files_subtotal = 0
            for filename in files:
                try:
                    node = parent[filename]
                    if self._need_to_upload(os.path.join(base, filename), parent[filename]):
                        retcode = node.delete()
                        self.logger.debug(f"_walk_local_drive, delete {node.name} returned {retcode}")
                        files_subtotal = files_subtotal + self._upload_file(base, filename, parent, "_walk_local_drive/local is newer")
                    self._update_md5(os.path.join(base, filename))

                except KeyError:
                    self.logger.debug(f"{os.path.join(base, filename)[len(base_path)+1:]} does not exist in iCloud")
                    _, db_file = os.path.split(database.DatabaseHandler.db_file)
                    if not filename == db_file:
                        files_subtotal = files_subtotal + self._upload_file(base, filename, parent,  "_walk_local_drive/new file")
                        self._update_md5(os.path.join(base, filename))

            if files_subtotal:
                self.logger.debug(f"getting children of {parent.name} in _walk_local_drive")
                parent.get_children(True)

            total_files_created = total_files_created + files_subtotal

        return (total_dirs_created, total_files_created)

    def sync_iCloudDrive(self, sync):
        self.setup()

        start = datetime.now()
        if sync == True:
            self.logger.info(f"syncing iCloud Drive to {self.directory} ...")
            (icloud_folders, icloud_files) = self._recurse_icloud_drive(self.pseudoroot, self.directory)
            self.logger.info(f"syncing {self.directory} to iCloud Drive ...")
            (local_folders, local_files) = self._walk_local_drive(self.pseudoroot, self.directory)
            self.logger.info(f"{icloud_folders} folders created locally from iCloud Drive")
            self.logger.info(f"{icloud_files} files downloaded locally from iCloud Drive")
            self.logger.info(f"{local_folders} folders created in iCloud Drive")
            self.logger.info(f"{local_files} files uploaded to iCloud Drive")
            self.logger.info(f"completed in {datetime.now() - start}")
        self.db.db_conn.close()
        self.db = None


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

@click.command(context_settings=CONTEXT_SETTINGS, options_metavar="<options>", no_args_is_help=True)
# @click.argument(
@click.option("-d", "--directory",     help="Local directory that should be used for download", type=click.Path(exists=True), metavar="<directory>")
@click.option("-u", "--username",      help="Your iCloud username or email address", metavar="<username>")
@click.option("-p", "--password",      help="Your iCloud password (default: use PyiCloud keyring or prompt for password)", metavar="<password>")
@click.option("--cookie-directory",    help="Directory to store cookies for authentication (default: ~/.pyicloud)", metavar="</cookie/directory>", default="~/.pyicloud")
@click.option("--pseudoroot", help="Folder within iCloud Drive to sync (i.e., exclude all other folders)", metavar="<pseudoroot>", default=None)
@click.option("--sleep-period",        help=f"Sleep period before checking if file system is dirty (default: {constants.SLEEP_PERIOD_MINUTES} minutes)", metavar="<sleep_period>", type=click.IntRange(1, 24*60), default=constants.SLEEP_PERIOD_MINUTES)
@click.option("--resync-period",       help=f"Resync to/from iCloud Drive every resync-period minutes (default: {constants.RESYNC_PERIOD_MINUTES} minutes)", metavar="<resync_period>", type=click.IntRange(1, 24*60), default=constants.RESYNC_PERIOD_MINUTES)
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
        pseudoroot,
        sleep_period,
        resync_period,
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
    logger.info(f"sleep_period: {sleep_period}")
    logger.info(f"resync_period: {resync_period}")
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

    icloud = None
    sync = True
    periods = 0
    while True:
        database.setup_database(directory)
        setup_database_logger()

        raise_authorization_exception = (
            smtp_username is not None
            or notification_email is not None
            or notification_script is not None
            or not sys.stdout.isatty()
        )

        if icloud == None:
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
            handler = iCloudDriveHandler(icloud.drive, directory, pseudoroot, log_level)
            logger.info(f"sync is {sync}")
            handler.sync_iCloudDrive(sync)
            logger.info(f"watching for filesystem change events, {periods}/{resync_period} minutes, will sleep for {sleep_period} minutes...")
            observer = Observer()
            observer.schedule(handler, path=directory, recursive=True)
            observer.start()
            try:
                while True:
                    time.sleep(sleep_period*60)
                    sync = False
                    periods = periods + sleep_period
                    # If the file-system changed while sleeping, set sync to true
                    if handler.is_dirty:
                        sync = True
                    elif periods >= resync_period:
                        sync = True
                        periods = 0
                    break;

            finally:
                observer.stop()
                observer.join()

        except PyiCloudAPIResponseException as ex:
            # For later: come up with a nicer message to the user. For now take the
            # exception text
            print(ex)
            sys.exit(constants.ExitCode.EXIT_FAILED_CLOUD_API.value)
