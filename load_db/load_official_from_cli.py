from oauth2client.client import OAuth2WebServerFlow
from oauth2client.tools import run_flow
from oauth2client.file import Storage
from tinydb import TinyDB, Query
import gspread
import time
import requests
import re


####################
# Initialize authentication with Google
CLIENT_ID = '48539282111-07fidfl1225gaiqk49ubb6r1fr21npln.apps.googleusercontent.com'
CLIENT_SECRET = 'CQ6-3PPwUjB6nZeYujAuqcWo'
# Set scope of permissions to accessing spreadsheets
scope = ['https://spreadsheets.google.com/feeds', 'https://docs.google.com/feeds']
redirect_uri = 'http://localhost:8080'

# Initialize the "flow" of the Google OAuth2.0 end user credentials
start_init_time = time.time()
flow = OAuth2WebServerFlow(client_id=CLIENT_ID,
                           client_secret=CLIENT_SECRET,
                           # scope='https://spreadsheets.google.com/feeds https://docs.google.com/feeds',
                           scope=scope,
                           redirect_uri=redirect_uri)

# store the credential, so you don't keep bugging the user
storage = Storage('creds.data')

# Authenticate end user from file first, if the credentials are valid
# NOTE: Flows don't seem to return a refresh token, so when the access
# token expires, you have to invoke user interaction
credentials = storage.get()
if not credentials or credentials.invalid or credentials.access_token_expired:
    credentials = run_flow(flow, storage)
####################


####################
# test URLs
list_of_urls = [
    "https://docs.google.com/spreadsheets/d/1kG9QTdus7LbpZP-3L9fNvwQ0nVpUUXyw7m7hpKSBH-E/edit#gid=2008460745",
    "https://docs.google.com/spreadsheets/d/1zJv0FYxoiC7YwgqIHmIsN_0h1UGkATi55hNDhM8WiSc/edit#gid=1988016352",
    "http://goo.gl/iR9kn2",
    # my v1 history doc, which should but doesn't load!
    #    "https://docs.google.com/spreadsheets/d/1TVMvg87wdvDz69v8StiEJt9iGJoayvSaYbvY-EswWlM/edit#gid=1",
]
####################


####################
# initialize config items

# utility definitions
# what do people write that means empty/no-value
blank_entries = [
    None,
    '',
    '-',
    'N/A',
    'N/a',
    'n/a',
    'None',
]
# which Last Revised dates are known to be Template v2.x revision dates
known_v2_revisions = [
    'Last Revised 2015',
    'Last Revised 2016',
    'Last Revised 2017-01-05',
]
# known Associations
known_associations = [
    'WFTDA',
    'MRDA',
    'Other',
]
game_types = [
    'Champs',
    'Playoff',
    'Sanc',
    'Reg',
    'Other',
]
ref_roles = [
    'THR',
    'CHR',
    'HR',
    'IPR',
    'JR',
    'OPR',
    'ALTR',
]
nso_roles = [
    'THNSO',
    'CHNSO',
    'HNSO',
    'PT',
    'PW',
    'IWB',
    'OWB',
    'JT',
    'SO',
    'SK',
    'PBM',
    'PBT',
    'LT'
    'ALTN'
]
known_roles = ref_roles + nso_roles
# TODO handle NSO families somehow - might be better places in query rather than storage
# nso_family = dict()
# nso_family['ch'] = ['CHNSO']
# nso_family['pt'] = ['PT', 'PW', 'IWB', 'OWB']
# nso_family['st'] = ['JT', 'SO', 'SK']
# nso_family['pm'] = ['PBM', 'PBT', 'LT']

# configure stale_time to determine if the history doc needs to be reloaded
SECONDS = 1
MINUTES = 60*SECONDS
HOURS = 60*MINUTES
DAYS = 24*HOURS
stale_time = time.time() - 2*MINUTES
# stale_time = time.time() - 5*DAYS
####################


####################
# Utility functions (to be moved out to another file)
####################
# utility function to unshorten URLs
def unshorten_url(url):
    '''
    Takes a URL, opens it in a web stream, and returns the eventual destination
    :param url: the URL (short or otherwise)
    :return: the destination URL
    '''
    return requests.head(url, allow_redirects=True).url
####################


####################
# Initialize datastores
offdb = TinyDB('officials_db.json')
OffQuery = Query()
# TODO: add in aliases db to track alternative names for finding them later
# TODO: add in metadata db to provide lookups
####################


####################
# All the support functions (to be moved to Google Sheets specific files)
####################
def get_version(sheet):
    """
    Check the info tabs and make a determination about which version of the officiating history document is being used.
    Different versions keep information in different places
    :param sheet: the loaded workbook
    :return: integer with the version number, or None for unknown version
    """
    sheet_list = map(lambda x: x.title, sheet.worksheets())
    if 'Summary' in sheet_list:
        if ('WFTDA Referee' in sheet_list) or ('WFTDA NSO' in sheet_list):
            # this is an old history doc but it's been modified to change the WFTDA Summary tab name
            return None
        elif 'Instructions' not in sheet_list:
            # this is a new history doc it's been modified to delete the instructions tab (a no no)
            return None
        # this is an edge case I found a couple of times in the excel export, it might not happen hitting the sheet directly
        # elif workbook['Instructions']['A1'].value == 'Loading...':
        #    # found one instance where the Instructions tab was showing "loading" - at the moment this will only happen on the new sheets
        #    return 2
        elif any(map(lambda x: x in sheet.worksheet('Instructions').acell('A104').value, known_v2_revisions)):
            return 2
        else:
            return None
    elif 'WFTDA Summary' in sheet_list:
        return 1
    else:
        return None


def get_name(sheet):
    """
    Picks through the fields looking for a name in preference order of:
        - derby name
        - real name
        - document title
    :param sheet: the connected Google Sheet
    :return: string with best guess at name
    """
    summary = sheet.worksheet("Summary")
    name = get_value(summary.acell('C4').value)
    # if the name is blank, fall back to real name
    if name is None:
        name = get_value(summary.acell('C3').value)
    # if the name is blank, fall back to doc title
    if name is None:
        name = sheet.title
    return name


def normalize_cert_endorsements(cert_string):
    """
    Takes the string from the cert endorsement cells and returns a list of standard endorsements, plus the strings that
    were not recognized
    :param cert_string: the raw string from the endorsement cells
    :return: list of recognized endorsements, plus a split of the remaining entries
    """
    # If there's nothing in the string, return None
    cert_string = get_value(cert_string)
    if cert_string is None:
        return None
    # TODO catalog the list of the past endorsement options

    # at the moment, just return a list of the raw strings
    return cert_string.split()


def normalize_cert_value(cert_string):
    """
    Takes the cert string from the history, which is a freeform field, and normalizes it to 1-5 or a blank for uncertified.
    Since certification is likely to be something different than 1-5, if there isn't an identifiable number in the cell
    return the contents of the cell. Once the results are seen, they can be normalized too
    :param cert_string: string taken directly from the history sheet
    :return: None, or 1-5, or a string literal of what they have in the cell
    """
    # If there's nothing in the cell, return None
    cert_string = get_value(cert_string)
    if cert_string is None:
        return None
    # if it's already a number, return an int (if it's < 1 or greater than 5, return None)
    elif isinstance(cert_string, float) or isinstance(cert_string, int):
        if (cert_string < 1) or (cert_string > 5):
            return None
        else:
            return int(cert_string)
    # if it's a string with numbers in it, return the first one
    numbers = re.findall(r'\d+', cert_string)
    if numbers:
        return int(numbers[0])
    else:
        # there are no numbers in the cell, look for someone spelling out the numbers:
        if cert_string.upper() == 'ONE':
            return 1
        elif cert_string.upper() == 'TWO':
            return 2
        elif cert_string.upper() == 'THREE':
            return 3
        elif cert_string.upper() == 'FOUR':
            return 4
        elif cert_string.upper() == 'FIVE':
            return 5
        else:
            # there are no valid numbers in the string, so return the string
            return cert_string


def get_value(value, datatype=None, enum=None):
    """
    Takes a string, and returns the value, or None if the content is equivaluent to the "empty string"
    :param value: raw spreadsheet value
    :param datatype: if the datatype is listed, the datatype is enforced on return value
    :param enum: a list containing valid values
    :return: interpreted value, as a string (by default) or datatype if listed
    """
    # if the passed in value is blank, return None
    if value in blank_entries:
        return None

    # if the enum is specified, return an entry in the list, or else None
    if enum:
        if value not in enum:
            return None

    # if the datatype is specified, return an entry in the that format, or else None
    if datatype:
        if isinstance(value, datatype):
            return value
        else:
            return None

    # if datatype is not specified, return the entry
    return value


def process_games(history):
    """
    Go through the history tab, and process each game entry and store it
    :param history: the history tab of the Google Sheet
    :return: a dict of game data
    """
    # Record which tab the record came from
    source = history.title

    games = list()
    rows = history.get_all_values()
    for row in rows[3:]:
        # If the line is blank, skip the whole row
        if not any(row):
            continue

        game = dict()
        val = get_value(row[0])
        if val:
            game['date'] = val

        val = get_value(row[1])
        if val:
            game['event'] = val

        val = get_value(row[2])
        if val:
            game['location'] = val

        val = get_value(row[3])
        if val:
            game['host_league'] = val

        val = get_value(row[4])
        if val:
            game['high_seed'] = val

        val = get_value(row[5])
        if val:
            game['low_seed'] = val

        val = get_value(row[6], enum=known_associations)
        if val:
            game['assn'] = val

        val = get_value(row[7], enum=game_types)
        if val:
            game['type'] = val

        val = get_value(row[8], enum=known_roles)
        if val:
            game['position'] = val

        val = get_value(row[9], enum=known_roles)
        if val:
            game['second_position'] = val

        val = get_value(row[10], enum=['Y'])
        if val:
            game['positional_software'] = val

        # tack the rest of the information on as "notes"
        game['notes'] = ':'
        val = get_value(row[11])
        if val:
            game['notes'] += val + ':'
        val = get_value(row[12])
        if val:
            game['notes'] += val + ':'
        val = get_value(row[13])
        if val:
            game['notes'] += val + ':'

        games.append(game)
    return games

def load_from_sheets(gconn, offdb, url):
    """
    This function takes a Google Sheets URL and using the API, parses it and returns a dict structure
    that represents the information taken from the sheet in a format suitable for loading into the tinydb (JSON).
    Currently supporting history docs version: 2.x (only)
    :param gconn: the connection to the Google Sheets API
    :param offdb: the Officials database
    :param url: the URL of the history document to be loaded
    :return: a tuple: (True, document) or (False, error string)
    """
    # open a new Google Sheet
    url = unshorten_url(url)
    try:
        sheet = gconn.open_by_url(url)
    except gspread.SpreadsheetNotFound:
        return False, "Can't find a spreadsheet there - might be v1 or not a Sheets URL"
    except Exception as e:
        return False, "Something went wrong trying to open the URL: {}".format(str(e))

    # check the history document template version
    # TODO split this off into a v2.py and a v3.py set of helper functions
    ver = get_version(sheet)
    if ver is None:
        return False, "Unidentified Document Format"
    elif ver == 1:
        return False, "Unsupported (old) Document Format"
    elif ver != 2:
        return False, "Unsupported (new) Document Format"

    # officials unique ID is the history doc ID
    id = sheet.id

    # does the official exist in the offdb already?
    # remove the entry first, to be replaced with the updated one
    # maybe one day there will be a historical track of the snapshots
    q = offdb.search(OffQuery.id == id)
    if len(q) > 0:
        print "Found existing entry for: " + q[0]['name']
        # print "last updated at: " + time.ctime(q[0]['last_updated'])
    
        # if they exist and the information is "fresh" then move along
        if q[0]['last_updated'] > stale_time:
            print "They're delightfully fresh, moving on..."
            return False, "{} is still current in db".format(q[0]['name'])
        else:
            print "They're a bit on the nose, getting a new copy..."
            offdb.remove((OffQuery.id == id))

    # basic info
    summary = sheet.worksheet("Summary")
    name = get_name(sheet)
    print "Loading: " + name
    off = dict()
    off['id'] = id
    off['name'] = name
    off['template_version'] = 2
    off['last_updated'] = time.time()

    off['league'] = get_value(summary.acell('C5').value)
    # TODO find a way to map league to location
    # off['location'] = ""
    # officiating since: currently free text for whatever they put in the cell
    # TODO look at "from dateutil import parser"
    # It might be able to interpret things, otherwise a series of strpfmt calls
    off['officiating_since'] = get_value(summary.acell('J4').value)
    # TODO calulate how many years of officiating, once I work out how to handle the random formats
    # off['officiating_years'] = ''

    # certification info
    ref_level = normalize_cert_value(summary.acell('C7').value)
    nso_level = normalize_cert_value(summary.acell('C8').value)
    if ref_level or nso_level:
        off['cert'] = dict()
        if ref_level:
            off['cert']['ref_level'] = ref_level
        if nso_level:
            off['cert']['nso_level'] = nso_level
        cert_endorsements = normalize_cert_endorsements(summary.acell('G7').value + ' ' + summary.acell('G8').value)
        if cert_endorsements:
            off['cert']['cert_endorsements'] = cert_endorsements

    # insurance information
    number = get_value(summary.acell('C6').value)
    provider = get_value(summary.acell('H6').value)
    if number or provider:
        off['insurance'] = dict()
        off['insurance']['number'] = get_value(summary.acell('C6').value)
        off['insurance']['provider'] = get_value(summary.acell('H6').value)

    # game information
    # TODO process the "Other" tab
    games = process_games(sheet.worksheet("Game History"))
    if games:
        off['games'] = games

    return True, off
####################


####################
# Start the meat of the loading work
####################
start_run_time = time.time()
# Open authenticated connection to the Google Sheets API
gconn = gspread.authorize(credentials)

# for each history doc, try to load it in the database
for url in list_of_urls:
    loaded_correctly, off = load_from_sheets(gconn, offdb, url)
    if loaded_correctly:
        offdb.insert(off)
    else:
        print "URL {} was not loaded because: {}".format(url, off)
print "Total time {:.2f}s (with an init time of {:.2f}s)".format((time.time() - start_init_time), (start_run_time - start_init_time))
