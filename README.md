# iCloud Drive Sync [![MIT License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

- A command-line tool to download synchronize your iCloud Drive.
- Works on Linux, Windows, and MacOS.
- Run as a [scheduled cron task](#cron-task) to keep a local backup of your iCloud Drive folders and contents

iCloud Drive Sync's basic operation is as follows:
1. Download changes from iCloud Drive to your local filesystem
2. Upload files from your local file system to iCloud Drive
3. Monitor local file system for changes and apply to iCloud Drive

iCloud Drive Sync first connects to the iCloud service and begins walking the folder structure in iCloud Drive. It creates local folders under the --directory you provide, if needed and downloads
files that have a modification date newer than those that exist. If the file does not exist locally, it is downloaded and its modification time is set to that of the iCloud Drive item. When complete,
iCloud Drive Sync then walks the directory structure under --directory and uploads files that are newer or don't exist in iCloud, including directories. When the upload phase is complete,
iCloud Drive Sync watches the local filesystem for changes and makes the corresponding add/delete/upload to iCloud Drive. iCloud Drive Sync does not monitor your iCloud Drive to download changes
from there. I have not figured out if that is possible in an elegant way yet.

## Install
`icloudds` depends on the python pyicloud library. 

`pyicloud` is a Python package that can be installed using `pip`, but it's borked as it only retrieves the first 200 albums in your iCloud library. Use my forked pyicloud implementation https://github.com/gordonaspin/pyicloud:

``` sh
git clone https://github.com/gordonaspin/pyicloud
cd pyicloud
pip install .
```

> If you need to install Python, see the [Requirements](#requirements) section for instructions.

## Usage

[//]: # (This is now only a copy&paste from --help output)

``` plain
$ python icloudds.py -h
Usage: icloudds.py <options>

  Synchronize local folder with iCloud Drive and watch for file system changes

Options:
  -d, --directory <directory>     Local directory that should be used for
                                  download
  -u, --username <username>       Your iCloud username or email address
  -p, --password <password>       Your iCloud password (default: use PyiCloud
                                  keyring or prompt for password)
  --cookie-directory </cookie/directory>
                                  Directory to store cookies for
                                  authentication (default: ~/.pyicloud)
  --sync                          Runs an initial scan of iCloud Drive and
                                  local filesystem
  --smtp-username <smtp_username>
                                  Your SMTP username, for sending email
                                  notifications when two-step authentication
                                  expires.
  --smtp-password <smtp_password>
                                  Your SMTP password, for sending email
                                  notifications when two-step authentication
                                  expires.
  --smtp-host <smtp_host>         Your SMTP server host. Defaults to:
                                  smtp.gmail.com
  --smtp-port <smtp_port>         Your SMTP server port. Default: 587 (Gmail)
                                  [x>=0]
  --smtp-no-tls                   Pass this flag to disable TLS for SMTP (TLS
                                  is required for Gmail)
  --notification-email <notification_email>
                                  Email address where you would like to
                                  receive email notifications. Default: SMTP
                                  username
  --notification-script PATH      Runs an external script when two factor
                                  authentication expires. (path required:
                                  /path/to/my/script.sh)
  --log-level [debug|info|error]  Log level (default: debug)
  --unverified-https              Overrides default https context with
                                  unverified https context
  --version                       Show the version and exit.
  -h, --help                      Show this message and exit.
```

Example:

``` sh
icloudds --directory ./Drive \
--username testuser@example.com \
--password pass1234 \
--directory Drive/ \
--sync
```

## Requirements

- Python 3.10+
- pip
- sqlite3

### Install Python & pip

#### Windows

- [Download Python 3.x](https://www.python.org/downloads/windows/)

#### Mac

- Install [Homebrew](https://brew.sh/) (if not already installed):

``` sh
which brew > /dev/null 2>&1 || /usr/bin/ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/master/install)"
```

- Install Python (includes `pip`):

``` sh
brew install python
```

> Alternatively, you can [download the latest Python 3.x installer for Mac](https://www.python.org/downloads/mac-osx/).

#### Linux (Ubuntu)

``` sh
sudo apt-get update
sudo apt-get install -y python
```

## Authentication

If your Apple account has two-factor authentication enabled,
you will be prompted for a code when you run the script.

Two-factor authentication will expire after an interval set by Apple,
at which point you will have to re-authenticate. This interval is currently two months.

Authentication cookies will be stored in a temp directory (`/tmp/pyicloud` on Linux, or `/var/tmp/...` on MacOS.) This directory can be configured with the `--cookie-directory` option.

You can receive an email notification when two-factor authentication expires by passing the
`--smtp-username` and `--smtp-password` options. Emails will be sent to `--smtp-username` by default,
or you can send to a different email address with `--notification-email`.

If you want to send notification emails using your Gmail account, and you have enabled two-factor authentication, you will need to generate an App Password at <https://myaccount.google.com/apppasswords>

### System Keyring

You can store your password in the system keyring using the `icloud` command-line tool
(installed with the `pyicloud` dependency):

``` plain
$ icloud --username jappleseed@apple.com
ICloud Password for jappleseed@apple.com:
Save password in keyring? (y/N)
```

If you have stored a password in the keyring, you will not be required to provide a password
when running the script.

If you would like to delete a password stored in your system keyring,
you can clear a stored password using the `--delete-from-keyring` command-line option:

``` sh
icloud --username jappleseed@apple.com --delete-from-keyring
```

## Error on first run

When you run the script for the first time, you might see an error message like this:

``` plain
Bad Request (400)
```

This error often happens because your account hasn't used the iCloud API before, so Apple's servers need to prepare some information about your iCloud Drive. This process can take around 5-10 minutes, so please wait a few minutes and try again.

If you are still seeing this message after 30 minutes, then please [open an issue on GitHub](https://github.com/gordonaspin/icloud_drive_sync/issues/new) and post the script output.

## Cron Task

Follow these instructions to run `icloudds` as a scheduled cron task.

``` sh
# Clone the git repo somewhere
git clone https://github.com/gordonaspin/icloud_drive_sync.git
cd icloud_drive_sync

# Copy the example cron script
cp cron_script.sh.example cron_script.sh
```

- Update `cron_script.sh` with your username, password, and other options

- Edit your "crontab" with `crontab -e`, then add the following line:

``` plain
0 */6 * * * /path/to/icloud_drive_sync/cron_script.sh
```

> If you provide SMTP credentials, the script will send an email notification
> whenever two-step authentication expires.

## Docker

This script is available in a Docker image:
`docker pull gordonaspin/icloudds:latest`

The iamge defines an entrypoint:
`ENTRYPOINT [ "icloudds", "-d", "/drive", "--cookie-directory", "/cookies" ]`

Usage:

```bash
# Downloads all iCloud Drive items to ./Drive

docker pull gordonaspin/icloudds:latest
docker run -it --name icloudds \
    -v $(pwd)/Drive:/drive \
    -v $(pwd)/cookies:/cookies \
    icloudds/icloudds:latest \
    --username testuser@example.com \
    --sync
```

On Windows:

- use `%cd%` instead of `$(pwd)`
- or full path, e.g. `-v c:/icloud/Drive:/data`

Building docker image from this repo and gordonaspin/pyicloud repo image locally:

```bash
docker build --tag your-repo/icloudds:latest --progress=plain -f ./Dockerfile.from_repo .

# run container forever in the background so we can keep a temporal keyring and cookies
# the keyring and cookies will exist until the container exits
docker run -it --detach --name icloud your-repo/icloudds sleep infinity

# the pyicloud icloudd command line utility
# this will optionally create a python keyring in the container for future use, cookies will go to a tmp folder in the container
docker exec -it icloud icloud --username apple_id@mail.com --llist

# run icloudds -h
docker exec -it icloud icloudds -h

# start the container with mounts for the Drive folder and cookie storage:
docker run -it --detach --name icloud -v ~/iCloud\ Drive/:/data -v ~/.pyicloud:/cookies your-repo/icloudds /bin/bash -c "while true; do sleep 60; done"

# run icloudds inside the container and download iCloud Drive items, for example:
docker exec -it icloud icloudds -d /data --cookie-directory /cookies -u apple_id@email.com --sync

```


Building original (from pre-fork) image locally:

```bash
docker build . -t icloudds
docker run -it --rm icloudds:latest icloudds --version
```

## Contributing

Want to contribute to iCloud Drive sync ? Awesome! Check out the [contributing guidelines](CONTRIBUTING.md) to get involved.
