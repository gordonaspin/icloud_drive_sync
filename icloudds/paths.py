"""Path functions"""
import os


def local_download_path(media, size, download_dir):
    """Returns the full download path, including size"""
    filename = filename_with_size(media, size)
    download_path = os.path.join(download_dir, filename)
    return download_path


def filename_with_size(media, size):
    """Returns the filename with size, e.g. IMG1234.jpg, IMG1234-small.jpg"""
    # Strip any non-ascii characters.
    filename = media.filename.encode("utf-8").decode("ascii", "ignore")
    if size == 'original':
        return filename
    return ("-%s." % size).join(filename.rsplit(".", 1))

def build_download_dir(directory, folder_structure, album, created_date, default_date):
    try:
        if folder_structure.lower() == "none":
            folder_path = ""
        elif folder_structure.lower() == "album":
            folder_path = album
        else:
            folder_path = folder_structure.format(created_date)
    except ValueError:  # pragma: no cover
        # This error only seems to happen in Python 2
        #logger.set_tqdm_description(f"{album}: photo {photo.filename} created date was not valid ({photo.created})", logging.ERROR)
        # e.g. ValueError: year=5 is before 1900
        # (https://github.com/icloud-photos-downloader/icloud_photos_downloader/issues/122)
        # Just use the Unix epoch
        folder_path = folder_structure.format(default_date)

    return os.path.normpath(os.path.join(directory, folder_path))