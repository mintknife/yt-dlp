from .common import InfoExtractor
from ..utils import ExtractorError


class CAM4IE(InfoExtractor):
    _VALID_URL = r'https?://(?:[^/]+\.)?cam4\.com/(?P<id>[a-z0-9_]+)'
    _TEST = {
        'url': 'https://www.cam4.com/foxynesss',
        'info_dict': {
            'id': 'foxynesss',
            'ext': 'mp4',
            'title': 're:^foxynesss [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}$',
            'age_limit': 18,
            'live_status': 'is_live',
            'thumbnail': 'https://snapshots.xcdnpro.com/thumbnails/foxynesss',
        },
    }

    def _real_extract(self, url):
        channel_id = self._match_id(url)

        # First check if the performer exists and their online status
        profile_info = self._download_json(
            f'https://www.cam4.com/rest/v1.0/profile/{channel_id}/info',
            channel_id, fatal=False, expected_status=404)

        if not profile_info:
            raise ExtractorError(f'{channel_id}: Performer not found', expected=True)

        is_online = profile_info.get('online', False)

        if not is_online:
            raise ExtractorError(f'{channel_id}: Performer is currently offline', expected=True)

        # Performer is online, try to get stream info
        stream_info = self._download_json(
            f'https://www.cam4.com/rest/v1.0/profile/{channel_id}/streamInfo',
            channel_id, fatal=False, expected_status=(204, 404))

        if not stream_info:
            raise ExtractorError(
                f'{channel_id}: Performer is online but not currently streaming', expected=True)

        m3u8_playlist = stream_info.get('cdnURL')
        if not m3u8_playlist:
            raise ExtractorError(
                f'{channel_id}: Stream info found but no playlist URL available', expected=True)

        formats = self._extract_m3u8_formats(m3u8_playlist, channel_id, 'mp4', m3u8_id='hls', live=True)

        return {
            'id': channel_id,
            'title': channel_id,
            'is_live': True,
            'age_limit': 18,
            'formats': formats,
            'thumbnail': f'https://snapshots.xcdnpro.com/thumbnails/{channel_id}',
        }
