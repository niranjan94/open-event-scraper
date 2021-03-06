import csv
import sys
import json
import datetime
import pytz
import simplejson
import validators
import jsonpickle
import logging
import parser
import urllib2
import os
from pprint import pprint
from models import *

# Track config is filename, header
# Should have all track information
# ID Prefix = should be unique across tracks
# Color code
SHEET_ID = os.environ['SHEET_ID']
SHEET_VERSIONING_GID = '1228727534'

# We assume each row represents a time interval of 30 minutes and use that to calculate end time
SESSION_LENGTH = datetime.timedelta(minutes=30)
TZ_UTC = pytz.utc
TZ_LOCAL = pytz.timezone('Europe/Berlin')

# Provide year of conference in case the date is impossible to parse
YEAR_OF_CONF = '2016'

def parse_tracklist(track_data):
    tracks = []
    headers = []
    HEADER_LINE = 1

    i = 1
    track = None
    for line in csv.reader(track_data.split("\n"), delimiter="\t"):
        if i == HEADER_LINE:
            HEADERS = map(str.strip, line)
        elif i > HEADER_LINE:
            row = create_associative_arr(line, HEADERS)
            if not row["Header Line"]:
                continue
            track = Track(
                id = i,
                name = row["Track"],
                header_line = int(row["Header Line"]),
                key_color = row["Key Color"],
                location = row["Room"],
                gid = row["GID"],
                order = i
            )
            tracks.append(track)

        i = i + 1

    return tracks

def parse_sessions(track, session_data):
    HEADERS = []
    # with open(track.filename) as tsv:
    i = 1
    speaker = None
    session = None

    # print session_data

    for line in csv.reader(session_data.split("\n"), delimiter="\t"):
        if i == track.header_line:
            HEADERS = map(str.strip, line)
        elif i > track.header_line:
            #   parse_row
            row = create_associative_arr(line, HEADERS)

            (res_speaker, res_session) = parse_row(row, speaker, session, track)

            if res_speaker is not None:
                speaker = res_speaker
            if res_session is not None:
                session = res_session
        i = i + 1


def create_associative_arr(line, headers):
    result = dict(zip(headers, line))
    return result

SPEAKERS = []
SESSIONS = []

GLOBAL_SPEAKER_IDS = {}

# Assume consequent rows with the same SessionID belong to the same session.
# - Start time is taken from first row, end time from last row + 30 minutes.
# - Title, description, etc. are taken from first row
# - Speaker data on additional rows is appended to the speaker list for that session
# - Rows w/o SessionId are skipped
def parse_row(row, last_speaker, last_session, current_track):

    session_id = row["SessionID"]

    # no session id => skip row
    if not session_id:
        return (None, None)

    if last_session is None or last_session.session_id != session_id:
        session = Session()
        session.session_id = session_id
        session.speakers = []
        SESSIONS.append(session)
    else:
        session = last_session

    if row["Given Name"]:
        speaker = Speaker()

        speaker.organisation = row["Company, Organization, Project or University"]
        speaker.name = (row["Given Name"].strip() + " " + row["Family Name"].strip()).strip()
        speaker.web = row["Website or Blog"]
        speaker.linkedin = parser.get_linkedin_url(row)
        speaker.biography = row["Please provide a short bio for the program"]
        speaker.github = ensure_social_link('https://github.com', row["github"])
        speaker.twitter = ensure_social_link('https://twitter.com', row["twitter"])
        speaker.country = row["Country/Region of Origin"]

        if hasattr(speaker, 'photo'):
            speaker.photo = validate_result(
                parser.get_pic_url(row),
                speaker.photo,
                "URL")
        else:
            speaker.photo = parser.get_pic_url(row)

            # Use email more reliable
        if GLOBAL_SPEAKER_IDS.has_key(speaker.name):
            id = GLOBAL_SPEAKER_IDS[speaker.name].id
            speaker.id = id
        else:
            SPEAKERS.append(speaker)
            id = len(GLOBAL_SPEAKER_IDS.keys()) + 1
            speaker.id = id
            GLOBAL_SPEAKER_IDS[speaker.name] = speaker
    else:
        speaker = None


    # set start time only the first time we encounter a session, but update end time for ever row
    # we encounter this session
    session_time = parse_time(row["Date"] + " " + row["Time"])

    if session_time is not None:
        session.end_time = (session_time + SESSION_LENGTH).isoformat() # TODO: +30 minutes
        if not hasattr(session, 'start_time'):
            session.start_time = session_time.isoformat()

    # only update attributes not already set
    if not hasattr(session, 'title'):
        maybe_title = row["Topic of your contribution"]
        if not maybe_title and speaker is not None:
            # print('use speaker name' + speaker.name)
            maybe_title = speaker.name
            speaker = None
        session.title = maybe_title

    if not hasattr(session, 'description'):
        session.description = row["Abstract of talk or project"]
    if not hasattr(session, 'sign_up'):
        if row.has_key('Sign up') and row['Sign up']:
            session.sign_up = row['Sign up']
        else:
            session.sign_up = None
    if not hasattr(session, 'video'):
        if row.has_key('Video') and row['Video']:
            session.video = row['Video']
        else:
            session.video = None
    if not hasattr(session, 'slides'):
        if row.has_key('Slideshow') and row['Slideshow']:
            session.slides = row['Slideshow']
        else:
            session.slides = None
    if not hasattr(session, 'type'):
        session.type = row["Type of Proposal"]
    if not hasattr(session, 'track'):
        session.track = {'id': track.id, 'name': track.name, 'order': track.order}
    if not hasattr(session, 'location'):
        if row.has_key('Location') and row['Location']:
            session.location = row['Location']
        else:
            session.location = track.location
    if speaker is not None:
        session.speakers.append({
            'name': speaker.name,
            'id': speaker.id,
            'organisation': speaker.organisation
        })

    return (speaker, session)


DATE_FORMATS = ["%Y %A %B %d %I:%M %p", "%Y %A %B %d %I.%M %p", "%A %B %d, %Y %I:%M"]

def parse_time(time_str):
    print time_str
    # Fix up year first, some of them may not have it
    iso_date = None
    if YEAR_OF_CONF not in time_str:
        time_str = YEAR_OF_CONF + " " + time_str

    for date_format in DATE_FORMATS:
        try:
            iso_date = datetime.datetime.strptime(time_str, date_format)
        except ValueError as e:
            pass
        else:
            break

    if iso_date is None:
        return None

    local_date = TZ_LOCAL.localize(iso_date)
    utc_date = local_date.astimezone(TZ_UTC)

    return utc_date


def validate_result(current, default, type):
    """
    Validates the data, whether it needs to be url, twitter, linkedin link etc.
    """
    if current is None:
        current = ""
    if default is None:
        default = ""
    if type == "URL" and validators.url(current, require_tld=True) and not validators.url(default, require_tld=True):
        return current
    if type == "EMAIL" and validators.email(current) and not validators.email(default):
        return current
    return default

def fetch_tsv_data(gid):
    base_url = 'https://docs.google.com/spreadsheets/d/' + SHEET_ID + '/export?format=tsv'
    url = base_url + '&gid=' + gid
    logging.info('GET ' + url)
    res = urllib2.urlopen(url)
    return res.read()

def write_json(filename, root_key, the_json):
    f = open(filename + '.json', 'w')
    the_json = simplejson.dumps(simplejson.loads(the_json), indent=2, sort_keys=False)
    json_to_write = '{ "%s":%s}' % (root_key, the_json)
    f.write(json_to_write)
    f.close()

def validate_sessions(sessions):
    logging.info('validating')

    s_map = {}
    dups = []

    for session in sessions:
        if session.session_id in s_map:
            s_map[session.session_id] = s_map[session.session_id] + 1
        else:
            s_map[session.session_id] = 1

    for session_id, count in s_map.iteritems():
        if count > 1:
            dups.append(session_id)

    if len(dups) > 0:
        logging.error('Duplicate session ids: ' + (', '.join(dups)))
        return False
    else:
        logging.info('All fine')
        return True

def ensure_social_link(website, link):
    """
    converts usernames of social profiles to full profile links
    if link is username, prepend website to it else return the link
    """
    if link == '' or link is None:
        return link
    if link.find('/') != -1: # has backslash, so not a username
        return link
    else:
        return website + '/' + link


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.DEBUG)

    logging.info("[Fetching Tracklist], gid = %s", SHEET_VERSIONING_GID)
    track_data = fetch_tsv_data(SHEET_VERSIONING_GID)
    tracks = parse_tracklist(track_data)

    i = 0
    for track in tracks:
        if not track.gid:
            continue
        # debug only, limit to a single track
        # if i > 0:
        #     break
        # i = i + 1

        logging.info("[Fetching Track] '%s', gid = %s", track.name, track.gid)
        data = fetch_tsv_data(track.gid)

        logging.info("[Parsing Track] '%s'", track.name)
        parse_sessions(track, data)
        logging.info('next...')

    if not validate_sessions(SESSIONS):
        sys.exit(1)

    # # print json.dumps(SPEAKERS[0].__dict__)
    logging.info('Writing %d speakers to out/speakers.json', len(SPEAKERS))
    speakers_json = jsonpickle.encode(SPEAKERS)
    write_json('out/speakers', 'speakers', speakers_json)

    logging.info('Writing %d sessions to out/sessions.json', len(SESSIONS))
    session_json = jsonpickle.encode(SESSIONS)
    write_json('out/sessions', 'sessions', session_json)

    logging.info('Writing %d tracks to out/tracks.json', len(tracks))
    tracks_json = jsonpickle.encode(tracks)
    write_json('out/tracks', 'tracks', tracks_json)
