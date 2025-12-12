#!/usr/bin/env python3
"""
CAM4 Standalone Stream Recorder

A lightweight standalone script to check CAM4 performer status and record streams.
No yt-dlp dependency required - only uses requests and ffmpeg.

Usage:
    python cam4_standalone.py <url> [output_file]
    
Examples:
    python cam4_standalone.py https://www.cam4.com/performer
    python cam4_standalone.py https://www.cam4.com/performer output.ts
"""

import re
import sys
import json
import subprocess
import argparse
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum

try:
    import requests
except ImportError:
    print("ERROR: requests library required. Install with: pip install requests")
    sys.exit(1)


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


class CAM4Standalone:
    """
    Standalone CAM4 stream checker and recorder.
    
    Replicates the functionality of the yt-dlp CAM4 extractor without
    requiring yt-dlp as a dependency.
    """
    
    VALID_URL_PATTERN = r'https?://(?:[^/]+\.)?cam4\.com/(?P<id>[a-z0-9_]+)'
    BASE_API_URL = "https://www.cam4.com/rest/v1.0/profile"
    THUMBNAIL_BASE_URL = "https://snapshots.xcdnpro.com/thumbnails"
    
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def _log(self, message: str):
        """Print message if verbose mode is enabled"""
        if self.verbose:
            print(f"[CAM4] {message}")
    
    def extract_username(self, url: str) -> str:
        """Extract username from CAM4 URL"""
        match = re.match(self.VALID_URL_PATTERN, url, re.IGNORECASE)
        if not match:
            raise CAM4Error(
                f"Invalid CAM4 URL: {url}",
                PerformerStatus.NOT_FOUND
            )
        return match.group('id').lower()
    
    def get_profile_info(self, username: str) -> dict:
        """
        Get performer profile information from API.
        
        Returns:
            dict with profile info or None if not found
        """
        url = f"{self.BASE_API_URL}/{username}/info"
        self._log(f"Fetching profile info for {username}")
        
        try:
            response = self.session.get(url, timeout=10)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except requests.exceptions.JSONDecodeError:
            return None
        except requests.exceptions.RequestException as e:
            self._log(f"Error fetching profile: {e}")
            return None
    
    def get_stream_info(self, username: str) -> Optional[dict]:
        """
        Get stream information from API.
        
        Returns:
            dict with stream info including cdnURL, or None if not streaming
        """
        url = f"{self.BASE_API_URL}/{username}/streamInfo"
        self._log(f"Fetching stream info for {username}")
        
        try:
            response = self.session.get(url, timeout=10)
            # 204 No Content means not streaming
            if response.status_code == 204:
                return None
            if response.status_code == 404:
                return None
            response.raise_for_status()
            data = response.json()
            return data if data else None
        except requests.exceptions.JSONDecodeError:
            return None
        except requests.exceptions.RequestException as e:
            self._log(f"Error fetching stream info: {e}")
            return None
    
    def verify_stream_accessible(self, m3u8_url: str) -> Tuple[bool, Optional[str]]:
        """
        Verify that the stream is accessible (not private/away).
        
        Returns:
            Tuple of (is_accessible, error_message)
        """
        self._log(f"Verifying stream accessibility")
        
        try:
            response = self.session.get(m3u8_url, timeout=10)
            content = response.text.lower()
            
            # CDN returns this message for private/away streams
            if 'not allowed to view' in content or 'session is not allowed' in content:
                return False, "Stream not accessible - performer may be in a private show or away"
            
            if response.status_code in (400, 403):
                return False, "Stream not accessible - performer may be in a private show or away"
            
            # Check if it's a valid m3u8 playlist
            if '#EXTM3U' in response.text:
                return True, None
            
            return False, "Invalid stream response"
            
        except requests.exceptions.RequestException as e:
            return False, f"Error accessing stream: {e}"
    
    def check_performer(self, url: str) -> PerformerInfo:
        """
        Check performer status and get stream URL if available.
        
        Args:
            url: CAM4 performer URL
            
        Returns:
            PerformerInfo with status and stream details
        """
        username = self.extract_username(url)
        thumbnail_url = f"{self.THUMBNAIL_BASE_URL}/{username}"
        
        # Check if performer exists
        profile = self.get_profile_info(username)
        if not profile:
            return PerformerInfo(
                username=username,
                status=PerformerStatus.NOT_FOUND,
                error_message=f"{username}: Performer not found"
            )
        
        # Check if online
        is_online = profile.get('online', False)
        if not is_online:
            return PerformerInfo(
                username=username,
                status=PerformerStatus.OFFLINE,
                thumbnail_url=thumbnail_url,
                error_message=f"{username}: Performer is currently offline"
            )
        
        # Get stream info
        stream_info = self.get_stream_info(username)
        if not stream_info:
            return PerformerInfo(
                username=username,
                status=PerformerStatus.ONLINE_NOT_STREAMING,
                thumbnail_url=thumbnail_url,
                error_message=f"{username}: Performer is online but not currently streaming"
            )
        
        # Get CDN URL
        cdn_url = stream_info.get('cdnURL')
        if not cdn_url:
            return PerformerInfo(
                username=username,
                status=PerformerStatus.ONLINE_NOT_STREAMING,
                thumbnail_url=thumbnail_url,
                error_message=f"{username}: Stream info found but no playlist URL available"
            )
        
        # Verify stream is accessible (not private/away)
        is_accessible, error = self.verify_stream_accessible(cdn_url)
        if not is_accessible:
            return PerformerInfo(
                username=username,
                status=PerformerStatus.PRIVATE_OR_AWAY,
                thumbnail_url=thumbnail_url,
                error_message=f"{username}: {error}"
            )
        
        # Stream is available!
        return PerformerInfo(
            username=username,
            status=PerformerStatus.STREAMING,
            stream_url=cdn_url,
            thumbnail_url=thumbnail_url
        )
    
    def record_stream(
        self,
        url: str,
        output_path: Optional[str] = None,
        ffmpeg_args: Optional[list] = None
    ) -> subprocess.Popen:
        """
        Start recording a CAM4 stream using ffmpeg.
        
        Args:
            url: CAM4 performer URL
            output_path: Output file path (default: {username}_{timestamp}.ts)
            ffmpeg_args: Additional ffmpeg arguments
            
        Returns:
            subprocess.Popen object for the ffmpeg process
            
        Raises:
            CAM4Error: If performer is not streaming
        """
        info = self.check_performer(url)
        
        if info.status != PerformerStatus.STREAMING:
            raise CAM4Error(info.error_message, info.status)
        
        # Generate output filename if not provided
        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = f"{info.username}_{timestamp}.ts"
        
        # Build ffmpeg command
        cmd = [
            'ffmpeg',
            '-i', info.stream_url,
            '-c:v', 'copy',
            '-c:a', 'copy',
            '-y'
        ]
        
        if ffmpeg_args:
            cmd.extend(ffmpeg_args)
        
        cmd.append(output_path)
        
        self._log(f"Starting recording: {output_path}")
        self._log(f"Stream URL: {info.stream_url}")
        
        # Start ffmpeg process
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        return process
    
    def download_thumbnail(self, url: str, output_path: Optional[str] = None) -> Optional[str]:
        """
        Download the performer's thumbnail.
        
        Args:
            url: CAM4 performer URL
            output_path: Output file path (default: {username}_thumb.jpg)
            
        Returns:
            Path to downloaded thumbnail or None if failed
        """
        username = self.extract_username(url)
        thumbnail_url = f"{self.THUMBNAIL_BASE_URL}/{username}"
        
        if not output_path:
            output_path = f"{username}_thumb.jpg"
        
        try:
            response = self.session.get(thumbnail_url, timeout=10)
            response.raise_for_status()
            
            with open(output_path, 'wb') as f:
                f.write(response.content)
            
            self._log(f"Thumbnail saved to {output_path}")
            return output_path
            
        except Exception as e:
            self._log(f"Error downloading thumbnail: {e}")
            return None


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='CAM4 Standalone Stream Recorder',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    Check performer status:
        python cam4_standalone.py --check https://www.cam4.com/performer
    
    Record stream:
        python cam4_standalone.py https://www.cam4.com/performer
        python cam4_standalone.py https://www.cam4.com/performer -o output.ts
    
    Download thumbnail:
        python cam4_standalone.py --thumbnail https://www.cam4.com/performer
        """
    )
    
    parser.add_argument('url', help='CAM4 performer URL')
    parser.add_argument('-o', '--output', help='Output file path')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose output')
    parser.add_argument('--check', action='store_true', help='Only check status, do not record')
    parser.add_argument('--thumbnail', action='store_true', help='Download thumbnail only')
    parser.add_argument('--json', action='store_true', help='Output status as JSON')
    
    args = parser.parse_args()
    
    cam4 = CAM4Standalone(verbose=args.verbose)
    
    try:
        if args.check or args.json:
            # Check mode - just show status
            info = cam4.check_performer(args.url)
            
            if args.json:
                result = {
                    'username': info.username,
                    'status': info.status.value,
                    'stream_url': info.stream_url,
                    'thumbnail_url': info.thumbnail_url,
                    'error': info.error_message
                }
                print(json.dumps(result, indent=2))
            else:
                if info.status == PerformerStatus.STREAMING:
                    print(f"✓ {info.username} is STREAMING")
                    print(f"  Stream URL: {info.stream_url}")
                else:
                    print(f"✗ {info.error_message}")
            
            sys.exit(0 if info.status == PerformerStatus.STREAMING else 1)
        
        elif args.thumbnail:
            # Thumbnail mode
            path = cam4.download_thumbnail(args.url, args.output)
            if path:
                print(f"Thumbnail saved to {path}")
                sys.exit(0)
            else:
                print("Failed to download thumbnail")
                sys.exit(1)
        
        else:
            # Record mode
            print(f"Starting recording...")
            process = cam4.record_stream(args.url, args.output)
            print(f"Recording started. Press Ctrl+C to stop.")
            
            try:
                process.wait()
            except KeyboardInterrupt:
                print("\nStopping recording...")
                process.terminate()
                process.wait()
                print("Recording stopped.")
            
            sys.exit(0)
    
    except CAM4Error as e:
        print(f"ERROR: {e.message}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)


if __name__ == '__main__':
    main()
