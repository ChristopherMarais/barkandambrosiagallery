from django.core.files.storage import FileSystemStorage

class WindowsDockerStorage(FileSystemStorage):
    """
    Custom storage to bypass permission/group checks that fail
    on Windows Docker volumes due to filesystem sync delays.
    """
    def _ensure_location_group_id(self, full_path):
        # Do nothing. This skips the 'os.stat' call that crashes on Windows.
        pass