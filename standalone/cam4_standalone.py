"""
CAM4 Standalone Stream Checker and Recorder

A lightweight module to check CAM4 performer status and record streams.
No yt-dlp dependency required - uses curl_cffi (preferred), FlareSolverr, or requests.

Usage:
    from cam4_standalone import CAM4Standalone, PerformerStatus
    
    cam4 = CAM4Standalone()
    info = cam4.check_performer("https://www.cam4.com/performer")
    
    if info.status == PerformerStatus.STREAMING:
        process = cam4.record_stream("https://www.cam4.com/performer", "output.ts")

Dependencies:
    - curl_cffi (recommended): pip install curl_cffi
    - FlareSolverr (optional): Docker container or standalone service
    - requests (fallback): pip install requests
    - ffmpeg (for recording)
"""

import re
import json
import subprocess
from datetime import datetime
from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum

# Try to use curl_cffi for browser impersonation (like yt-dlp)
USING_CURL_CFFI = False
try:
    from curl_cffi import requests as curl_requests
    USING_CURL_CFFI = True
except ImportError:
    curl_requests = None

try:
    import requests
except ImportError:
    requests = None
    if not USING_CURL_CFFI:
        raise ImportError(
            "Either curl_cffi or requests library required. "
            "Install with: pip install curl_cffi (recommended) or pip install requests"
        )


class PerformerStatus(Enum):
    """Possible performer statuses"""
    NOT_FOUND = "not_found"
    OFFLINE = "offline"
    ONLINE_NOT_STREAMING = "online_not_streaming"
    PRIVATE_OR_AWAY = "private_or_away"
    STREAMING = "streaming"


@dataclass
class PerformerInfo:
    """Information about a CAM4 performer"""
    username: str
    status: PerformerStatus
    stream_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    error_message: Optional[str] = None


class CAM4Error(Exception):
    """Custom exception for CAM4 related errors"""
    def __init__(self, message: str, status: PerformerStatus):
        self.message = message
        self.status = status
        super().__init__(message)


class FlareSolverrClient:
    """
    Client for FlareSolverr - a proxy that uses a real browser
    to bypass Cloudflare and other anti-bot protections.
    """
    
    def __init__(self, base_url: str = "http://localhost:8191"):
        self.base_url = base_url.rstrip('/')
    
    def get(self, url: str, timeout: int = 10) -> 'FlareSolverrResponse':
        """Make a GET request through FlareSolverr."""
        payload = {"cmd": "request.get", "url": url, "maxTimeout": timeout * 1000}
        response = requests.post(f"{self.base_url}/v1", json=payload, timeout=60)
        response.raise_for_status()
        result = response.json()
        
        if result.get("status") != "ok":
            raise Exception(f"FlareSolverr error: {result.get('message', 'Unknown error')}")
        
        solution = result.get("solution", {})
        return FlareSolverrResponse(
            status_code=solution.get("status", 200),
            text=solution.get("response", ""),
            url=solution.get("url", url)
        )
    
    def is_available(self) -> bool:
        """Check if FlareSolverr is available"""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            return response.status_code == 200
        except:
            return False


class FlareSolverrResponse:
    """Response-like object for FlareSolverr results"""
    
    def __init__(self, status_code: int, text: str, url: str):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.content = text.encode('utf-8')
    
    def json(self) -> dict:
        return json.loads(self.text)
    
    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP Error {self.status_code}")


class CAM4Standalone:
    """
    Standalone CAM4 stream checker and recorder.
    
    Request priority:
    1. curl_cffi (browser impersonation)
    2. FlareSolverr (real browser proxy)
    3. requests (plain HTTP)
    
    Authentication:
    - Pass cookies_file for Netscape format cookie file (exported from browser)
    - Pass cookies dict for direct cookie values
    """
    
    VALID_URL_PATTERN = r'https?://(?:[^/]+\.)?cam4\.com/(?P<id>[a-z0-9_]+)'
    BASE_API_URL = "https://www.cam4.com/rest/v1.0/profile"
    THUMBNAIL_BASE_URL = "https://snapshots.xcdnpro.com/thumbnails"
    
    def __init__(
        self, 
        verbose: bool = False, 
        impersonate: str = "chrome",
        flaresolverr_url: Optional[str] = None,
        use_flaresolverr: bool = False,
        cookies_file: Optional[str] = None,
        cookies: Optional[dict] = None
    ):
        self.verbose = verbose
        self._flaresolverr = None
        self._use_flaresolverr = use_flaresolverr
        
        if flaresolverr_url:
            self._flaresolverr = FlareSolverrClient(flaresolverr_url)
            if not self._flaresolverr.is_available():
                self._log(f"FlareSolverr not responding at {flaresolverr_url}")
                self._flaresolverr = None
        
        if USING_CURL_CFFI and not use_flaresolverr:
            self.session = curl_requests.Session(impersonate=impersonate)
        else:
            self.session = requests.Session()
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
        
        # Load cookies from file (Netscape format)
        if cookies_file:
            self._load_cookies_from_file(cookies_file)
        
        # Set cookies from dict
        if cookies:
            for name, value in cookies.items():
                self.session.cookies.set(name, value, domain='.cam4.com')
    
    def _load_cookies_from_file(self, filepath: str):
        """
        Load cookies from a Netscape format cookie file.
        This format is used by browser extensions like "Get cookies.txt" or "cookies.txt".
        """
        try:
            with open(filepath, 'r') as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if not line or line.startswith('#'):
                        continue
                    
                    parts = line.split('\t')
                    if len(parts) >= 7:
                        domain, _, path, secure, expires, name, value = parts[:7]
                        # Only load cam4.com cookies
                        if 'cam4.com' in domain:
                            self.session.cookies.set(
                                name, value, 
                                domain=domain,
                                path=path,
                                secure=(secure.upper() == 'TRUE')
                            )
            self._log(f"Loaded cookies from {filepath}")
        except Exception as e:
            self._log(f"Error loading cookies from {filepath}: {e}")
    
    def _log(self, message: str):
        if self.verbose:
            print(f"[CAM4] {message}")
    
    def _make_request(self, url: str, timeout: int = 10):
        if self._use_flaresolverr and self._flaresolverr:
            try:
                return self._flaresolverr.get(url, timeout=timeout)
            except Exception as e:
                self._log(f"FlareSolverr failed: {e}")
        return self.session.get(url, timeout=timeout)
    
    def extract_username(self, url: str) -> str:
        """Extract username from CAM4 URL"""
        match = re.match(self.VALID_URL_PATTERN, url, re.IGNORECASE)
        if not match:
            raise CAM4Error(f"Invalid CAM4 URL: {url}", PerformerStatus.NOT_FOUND)
        return match.group('id').lower()
    
    def get_profile_info(self, username: str) -> Optional[dict]:
        """Get performer profile information from API."""
        try:
            response = self._make_request(f"{self.BASE_API_URL}/{username}/info")
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except:
            return None
    
    def get_stream_info(self, username: str) -> Optional[dict]:
        """Get stream information from API."""
        try:
            response = self._make_request(f"{self.BASE_API_URL}/{username}/streamInfo")
            if response.status_code in (204, 404):
                return None
            response.raise_for_status()
            return response.json() or None
        except:
            return None
    
    def verify_stream_accessible(self, m3u8_url: str) -> Tuple[bool, Optional[str]]:
        """Verify that the stream is accessible (not private/away)."""
        try:
            response = self._make_request(m3u8_url)
            content = response.text.lower()
            
            if 'not allowed to view' in content or 'session is not allowed' in content:
                return False, "Stream not accessible - performer may be in a private show or away"
            if response.status_code in (400, 403):
                return False, "Stream not accessible - performer may be in a private show or away"
            if '#EXTM3U' in response.text:
                return True, None
            return False, "Invalid stream response"
        except Exception as e:
            return False, f"Error accessing stream: {e}"
    
    def check_performer(self, url: str) -> PerformerInfo:
        """Check performer status and get stream URL if available."""
        username = self.extract_username(url)
        thumbnail_url = f"{self.THUMBNAIL_BASE_URL}/{username}"
        
        profile = self.get_profile_info(username)
        if not profile:
            return PerformerInfo(username, PerformerStatus.NOT_FOUND, 
                                error_message=f"{username}: Performer not found")
        
        if not profile.get('online', False):
            return PerformerInfo(username, PerformerStatus.OFFLINE, thumbnail_url=thumbnail_url,
                                error_message=f"{username}: Performer is currently offline")
        
        stream_info = self.get_stream_info(username)
        if not stream_info or not stream_info.get('cdnURL'):
            return PerformerInfo(username, PerformerStatus.ONLINE_NOT_STREAMING, thumbnail_url=thumbnail_url,
                                error_message=f"{username}: Performer is online but not currently streaming")
        
        cdn_url = stream_info['cdnURL']
        is_accessible, error = self.verify_stream_accessible(cdn_url)
        if not is_accessible:
            return PerformerInfo(username, PerformerStatus.PRIVATE_OR_AWAY, thumbnail_url=thumbnail_url,
                                error_message=f"{username}: {error}")
        
        return PerformerInfo(username, PerformerStatus.STREAMING, stream_url=cdn_url, thumbnail_url=thumbnail_url)
    
    def record_stream(self, url: str, output_path: Optional[str] = None, 
                      ffmpeg_args: Optional[list] = None) -> subprocess.Popen:
        """Start recording a CAM4 stream using ffmpeg."""
        info = self.check_performer(url)
        if info.status != PerformerStatus.STREAMING:
            raise CAM4Error(info.error_message, info.status)
        
        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"{info.username}_{timestamp}.ts"
        
        cmd = ['ffmpeg', '-i', info.stream_url, '-c:v', 'copy', '-c:a', 'copy', '-y']
        if ffmpeg_args:
            cmd.extend(ffmpeg_args)
        cmd.append(output_path)
        
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    def download_thumbnail(self, url: str, output_path: Optional[str] = None) -> Optional[str]:
        """Download the performer's thumbnail."""
        username = self.extract_username(url)
        if not output_path:
            output_path = f"{username}_thumb.jpg"
        
        try:
            response = self.session.get(f"{self.THUMBNAIL_BASE_URL}/{username}", timeout=10)
            response.raise_for_status()
            with open(output_path, 'wb') as f:
                f.write(response.content)
            return output_path
        except:
            return None
