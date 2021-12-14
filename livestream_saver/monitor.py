from pathlib import Path
from time import sleep
from random import uniform
import logging
from typing import Optional, Any, List, Dict
from livestream_saver import extract

logger = logging.getLogger(__name__)


class YoutubeChannel:
    def __init__(self, URL, channel_id, session, output_dir: Path = Path(), hooks={}):
        self.session = session
        self.url = URL
        self.id = channel_id
        self.videos_json = None
        self._community_videos = None
        self._public_videos = None
        self._upcoming_videos = None

        self._community_videos_html = None
        self._public_videos_html = None
        self._upcoming_videos_html = None

        self._community_json = None
        self._public_json = None
        self._upcoming_json = None

        self.hooks = hooks
        self._hooked_videos = []
        self.output_dir = output_dir
        self.logger = logger

    def get_channel_name(self):
        """Get the name of the channel from the community JSON (once retrieved).
        """
        # FIXME this method pre-fetches the json if called before
        # TODO handle channel names which are not IDs
        # => get "videos" tab html page and grab externalId value from it?
        _json = self.public_json
        if not _json:
            _json = self.community_json

        if _json:
            return _json.get('metadata', {})\
                        .get('channelMetadataRenderer', {})\
                        .get('title')
        return "Unknown"

    @property
    def community_videos_html(self):
        if self._community_videos_html:
            return self._community_videos_html

        self._community_videos_html = self.session.make_request(
            self.url + '/community'
        )
        return self._community_videos_html

    @property
    def public_videos_html(self):
        if self._public_videos_html:
            return self._public_videos_html

        self._public_videos_html = self.get_public_livestreams('current')
        return self._public_videos_html

    @property
    def upcoming_videos_html(self):
        if self._upcoming_videos_html:
            return self._upcoming_videos_html

        self._upcoming_videos_html = self.get_public_livestreams('upcoming')
        return self._upcoming_videos_html

    @property
    def community_json(self) -> Dict:
        if self._community_json:
            return self._community_json

        self._community_json = extract.str_as_json(
            extract.initial_player_response(
                self.community_videos_html
            )
        )
        return self._community_json

    @property
    def public_json(self):
        if self._public_json:
            return self._public_json

        self._public_json = extract.str_as_json(
            extract.initial_player_response(
                self.public_videos_html
            )
        )
        return self._public_json

    @property
    def upcoming_json(self):
        if self._upcoming_json:
            return self._upcoming_json

        self._upcoming_json = extract.str_as_json(
            extract.initial_player_response(
                self.upcoming_videos_html
            )
        )
        return self._upcoming_json

    @property
    def community_videos(self):
        community_videos = self.update_community_videos()

        if self._community_videos is None:
            # Log only the very first time
            logger.info(
                "Currently listed community videos: {}\n{}".format(
                    len(community_videos),
                    format_list_output(community_videos)
                )
            )
        else:
            new_comm_videos = [
                v for v in community_videos if v not in self._community_videos
            ]
            if new_comm_videos:
                logger.info(
                    "Newly added community video: {}\n{}".format(
                        len(new_comm_videos),
                        format_list_output(new_comm_videos)
                    )
                )
                for vid in new_comm_videos:
                    self.trigger_hook('on_video_detected', vid)
        self._community_videos = community_videos
        return self._community_videos

    @property
    def public_videos(self):
        public_videos = self.update_public_videos()

        if self._public_videos is None:
            # Log only the very first time
            logger.info(
                "Currently listed public videos: {}\n{}".format(
                    len(public_videos),
                    format_list_output(public_videos)
                )
            )
        else:
            new_pub_videos = [
                v for v in public_videos if v not in self._public_videos
            ]
            if new_pub_videos:
                logger.info(
                    "Newly added public video: {}\n{}".format(
                        len(new_pub_videos),
                        format_list_output(new_pub_videos)
                    )
                )
                for vid in new_pub_videos:
                    if vid.get("isLiveNow") or vid.get("isLive"):
                        continue
                    # This should only trigger for VOD (non-live) videos
                    self.trigger_hook('on_video_detected', vid)
        self._public_videos = public_videos
        return self._public_videos

    @property
    def upcoming_videos(self) -> List[Dict]:
        upcoming_videos: List[Dict] = self.update_upcoming_videos()

        if self._upcoming_videos is None:
            # Log only the very first time
            logger.info(
                "Currently listed upcoming videos: {}\n{}".format(
                    len(upcoming_videos),
                    format_list_output(upcoming_videos)
                )
            )
            for vid in upcoming_videos:
                # These checks are important to avoid the youtube bug that
                # return VODs if there is no upcoming video at all.
                # FIXME perhaps API calls might help solve this, otherwise we
                # could keep track of public videoIds and ignore them. 
                if vid.get('upcoming') and vid.get('isLive'):
                    self.trigger_hook('on_upcoming_detected', vid)
        else:
            new_upcoming_videos = [
                v for v in upcoming_videos if v not in self._upcoming_videos
            ]
            if new_upcoming_videos:
                logger.info(
                    "Newly added upcoming videos: {}\n{}".format(
                        len(new_upcoming_videos),
                        format_list_output(new_upcoming_videos)
                    )
                )
                for vid in new_upcoming_videos:
                    # These checks are important to avoid the youtube bug that
                    # return VOD here as well.
                    if vid.get('upcoming') and vid.get('isLive'):
                        self.trigger_hook('on_upcoming_detected', vid)
        self._upcoming_videos = upcoming_videos
        return self._upcoming_videos

    def trigger_hook(self, hook_name: str, vid):
        if hook := self.hooks.get(hook_name, None):
            if not self.is_hooked_video(vid.get("videoId", None)):
                url = vid.get("url")
                # Make an API request to fetch the description
                description = vid.get("description")
                if not description:
                    if metadata := self.get_video_metadata(vid.get("videoId")):
                        vid["description"] = self.get_description_metadata(metadata)

                args = {
                    "url": f"https://www.youtube.com{url}" if url is not None else None,
                    "cookie_path": self.session.cookie_path,
                    "logger": self.logger,
                    "output_dir": self.output_dir,
                    "title": vid.get("title"),
                    "description": vid.get("description")
                }
                hook.spawn_subprocess(args)

    @staticmethod
    def get_description_metadata(json: Dict) -> Optional[str]:
        return json.get('videoDetails', {})\
                   .get("shortDescription")

    def get_video_metadata(self, videoId: Optional[str]) -> Optional[Dict]:
        """Fetch more details about a particular video ID."""
        if not videoId:
            return None
        try:
            json_string = self.session.make_api_request(videoId)
            return extract.str_as_json(json_string)
        except Exception as e:
            logger.warning(f"Error fetching metadata for video {videoId}: {e}")
            return None

    def is_hooked_video(self, videoId: Optional[str]):
        """Keep track of the last few videos for which we have triggered a hook
        command already, in a circular buffer to avoid growing infinitely and
        triggering again for the same video."""
        if not videoId:
            # ignore if missing entry, avoid calling hook
            return True
        if videoId in self._hooked_videos:
            return True
        self._hooked_videos.append(videoId)
        # Limit the buffer conserve memory
        if len(self._hooked_videos) >= 40:
            self._hooked_videos.pop(0)

    def filter_videos(self, filter_type: str = 'isLiveNow') -> List:
        """Returns a list of videos that are live, from all channel tabs combined.
        Usually there is only one live video active at a time.
        """
        live_videos = []
        for vid in self.community_videos:
            if vid.get(filter_type):
                live_videos.append(vid)
        for vid in self.public_videos:
            if vid.get(filter_type):
                live_videos.append(vid)
        return live_videos

    def update_community_videos(self):
        """Returns list of Dict with urls to videos attached to community posts.
        """
        self._community_json = self._community_videos_html = None # force update

        community_json = {}
        try:
            community_json = self.community_json
            self.session.is_logged_out(community_json)
        except Exception as e:
            # TODO send email here?
            logger.critical(f"Got an invalid community JSON: {e}")

        tabs = get_tabs_from_json(community_json)
        return get_videos_from_tab(tabs, 'Community')

    def update_public_videos(self):
        """Returns list of videos from "videos" or "featured" tabs."""
        self._public_json = self._public_videos_html = None # force update

        public_json = {}
        try:
            public_json = self.public_json
        except Exception as e:
            # TODO send email here?
            logger.critical(f"Got an invalid public JSON: {e}")

        tabs = get_tabs_from_json(public_json)
        return get_videos_from_tab(tabs, 'Videos')

    def update_upcoming_videos(self):
        """Returns list of videos from "videos" or "featured" tabs."""
        self._upcoming_json = self._upcoming_videos_html = None # force update

        upcoming_json = {}
        try:
            upcoming_json = self.upcoming_json
        except Exception as e:
            # TODO send email here?
            logger.critical(f"Got an invalid upcoming JSON: {e}")

        tabs = get_tabs_from_json(upcoming_json)
        return get_videos_from_tab(tabs, 'Videos')

    def get_public_livestreams(self, filtertype):
        """Fetch publicly available streams from the channel pages."""
        if filtertype == 'upcoming':
            # https://www.youtube.com/c/kamikokana/videos\?view\=2\&live_view\=502
            # https://www.youtube.com/channel/UCoSrY_IQQVpmIRZ9Xf-y93g/videos?view=2&live_view=502
            # This video tab filtered list, returns public upcoming livestreams (with scheduled times)
            return self.session.make_request(
                self.url + '/videos?view=2&live_view=502'

            )
        if filtertype == 'current':
            # NOTE: active livestreams are also displays in /featured tab:
            # https://www.youtube.com/c/kamikokana/videos?view=2&live_view=501
            return self.session.make_request(
                self.url + '/videos?view=2&live_view=501'
            )
        if filtertype == 'featured':
            # NOTE "featured" tab is ONLY reliable for CURRENT live streams
            return self.session.make_request(
                self.url + '/featured'
            )
        # NOTE "/live" virtual tab is a redirect to the current live broadcast


def get_videos_from_tab(tabs, tabtype) -> List[Dict]:
    """
    Returns videos attached to posts in available "tab" section in JSON response.
    tabtype is either "Videos" "Community", "Home" etc.
    """
    videos = []
    for tab in tabs:
        if tab.get('tabRenderer', {}).get('title') != tabtype:
            continue
        sectionList_contents = tab.get('tabRenderer')\
                      .get('content', {})\
                      .get('sectionListRenderer', {})\
                      .get('contents', [])
        for _item in sectionList_contents:
            for __item in _item.get('itemSectionRenderer', {}).get('contents', []):
                if tabtype == "Community":
                    post = __item.get('backstagePostThreadRenderer', {})\
                                 .get('post', {})
                    if post:
                        video_attachement = get_video_from_post(
                            post.get('backstagePostRenderer', {})\
                                .get('backstageAttachment', {})\
                                .get('videoRenderer', {})
                            )
                        if video_attachement.get('videoId'):
                            # some posts don't have attached videos
                            videos.append(video_attachement)
                elif tabtype == "Videos":
                    griditems = __item.get('gridRenderer', {})\
                                      .get('items', [])
                    for griditem in griditems:
                        video_attachement = get_video_from_post(
                            griditem.get('gridVideoRenderer')
                        )
                        if video_attachement.get('videoId'):
                            videos.append(video_attachement)
    return videos


def get_video_from_post(attachment: Dict) -> Dict[str, Any]:
    if not attachment:
        return {}
    video_post = {}
    video_post['videoId'] = attachment.get('videoId')
    for _item in attachment.get('title', {}).get('runs', []):
        if _item.get('text'): # assumes list with only one item
            video_post['title'] = _item.get('text')
    video_post['url'] = attachment.get('navigationEndpoint', {})\
                                    .get('commandMetadata', {})\
                                    .get('webCommandMetadata', {})\
                                    .get('url')
    if eventData := attachment.get('upcomingEventData', {}):
        # we can safely assume it is "upcoming"
        video_post['upcoming'] = True
        video_post['startTime'] = eventData.get('startTime')

    # Attempt to attach "live" and "upcoming" status from the response
    for _item in attachment.get('thumbnailOverlays', []):
        if status_renderer := _item.get('thumbnailOverlayTimeStatusRenderer', {}):
            if style := status_renderer.get('style'):
                # This seems to be a decent indicator that it is currently LIVE
                if style == 'LIVE':
                    video_post['isLiveNow'] = True
                # This might be redundant with upcomingEventData key
                elif style == 'UPCOMING':
                    video_post['upcoming'] = True
            if text := status_renderer.get('text'):
                if runs := text.get('runs', []):
                    if len(runs) > 0 and runs[0].get('text') == 'LIVE':
                        # This indicates that it should be live in the future
                        video_post['isLive'] = True
            break
    # Another way to check if it is currently LIVE
    for _item in attachment.get('badges', []):
        if badge_renderer := _item.get('metadataBadgeRenderer', {}):
            if badge_renderer.get('label') == "LIVE NOW":
                video_post['isLiveNow'] = True

    return video_post


def get_tabs_from_json(_json):
    if not _json:
        return _json
    return _json.get('contents', {}) \
                .get('twoColumnBrowseResultsRenderer', {}) \
                .get('tabs', [])


def rss_from_id(channel_id):
    # This endpoint doesn't show member streams.
    # It does show public streams, but we don't know it's a stream until we start
    # actually downloading it.
    # WARNING: this seems to be a legacy API, might get deprecated someday!
    return 'https://www.youtube.com/feeds/videos.xml?channel_id=' + channel_id


def rss_from_name(channel_name):
    return 'https://www.youtube.com/feeds/videos.xml?user=' + channel_name


def wait_block(min_minutes=15.0, variance=3.5):
    """
    Sleep (blocking) for a specified amount of minutes,
    with variance to avoid being detected as a robot.
    :param min_minutes float Minimum number of minutes to wait.
    :param variance float Maximum number of minutes added.
    """
    min_seconds = min_minutes * 60
    max_seconds = min_seconds + (variance * 60)
    wait_time_sec = uniform(min_seconds, max_seconds)
    wait_time_min = wait_time_sec / 60
    logger.info(f"Sleeping for {wait_time_min:.2f} minutes ({wait_time_sec:.2f} seconds)...")
    sleep(wait_time_sec)


def format_list_output(vid_list: List[Dict]) -> str:
    strs = []
    for vid in vid_list:
        strs.append(
            f"{vid.get('videoId')} - {vid.get('title')}"
            f"{' (Livestream)' if vid.get('isLive') else ''}"
            f"{' LIVE NOW' if vid.get('isLiveNow') else ''}")
    return "\n".join(strs)
