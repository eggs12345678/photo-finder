import os
import sys
import tempfile
from pathlib import Path

# Configure the data directory before photofinder.config is imported.
os.environ.setdefault("PHOTOFINDER_DATA", tempfile.mkdtemp(prefix="photofinder-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
