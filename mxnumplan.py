"""
Mexico in August 2019 will change their national numbering plan to a closed fixed length (10 digit) numbering
plan:  https://www.itu.int/dms_pub/itu-t/oth/02/02/T020200008A0003PDFE.pdf

While previously cellphone numbers were easily identifiable by their common prefix in the new numbering plan
cellphone  numbers share the same prefix(es) as the other geographical numbers. Enterprise admins still want to be
able  to determine which number are cellphone numbers to be able to implement differentiated class of service (with
or without access to cellphone numbers).

One way to achieve that is to provision blocking translation patterns in UCM to block access to mobile cellphone
number ranges. This partition with blocking patterns can then be used to built a "no cellphone" class of service.
The question remains: What are the cellphone number ranges?

To answer that question the Mexcian numbering plan authority publishes a CSV file with all number ranges in Mexico
at:  https://sns.ift.org.mx:8081/sns-frontend/planes-numeracion/descarga-publica.xhtml. This list is continuously
updated.

This Python script:

* pulls the latest numbering plan from the website
* identifies the mobile ranges
* summarizes these ranges to a minimal set of patterns
* provisions blocking translation patterns or route patterns for all of these patterns
"""
import zipfile
import ucmaxl
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from tempfile import TemporaryFile
from csv import DictReader
from io import TextIOWrapper, RawIOBase
from typing import Iterable, Generator, List, Tuple
from itertools import chain
import argparse
import logging
import os
import re
from collections import OrderedDict
import cgi
import urllib3
import functools
from tqdm import tqdm

BASE_URL = 'https://sns.ift.org.mx:8081/sns-frontend/planes-numeracion/descarga-publica.xhtml'
PARTITION_NAME = 'mobile'


def patterns_from_zip(file: RawIOBase) -> Generator[OrderedDict, None, None]:
    """
    Yield patterns from 1st file(CSV) of a given ZIP file
    :param file:
    :return:
    """
    with zipfile.ZipFile(file, mode='r') as zip:
        # decompress and read the 1st file in the zip
        file_name = zip.filelist[0].filename
        print(f'Reading number ranges from {file_name}...')
        with zip.open(name=file_name) as csv_file:
            text_file = TextIOWrapper(csv_file, encoding='utf8', newline='')
            dict_reader = DictReader(text_file)
            for p in dict_reader:
                yield p
            # for
        # with
    # with
    return


def patterns_from_web() -> Generator[OrderedDict, None, None]:
    """
    Read ZIP file from Mexican numbering plan authority web site and yield patterns from that ZIP
    :return:
    """
    print(f'Accessing numbering plan information web site at {BASE_URL} ...')
    session = requests.Session()
    r = session.get(BASE_URL)
    soup = BeautifulSoup(r.text, 'lxml')

    # there is a form in there which we need to submit
    form = soup.find('form', id='FORM_planes')
    assert form is not None

    action = form['action']

    inputs = form.find_all('input')
    form_data = {input['name']: input['value'] for input in inputs}

    button = form.find('button')
    form_data[button['name']] = ''

    action_url = urljoin(BASE_URL, action)
    print('Requesting ZIP from web site...')
    with session.post(action_url, data=form_data, stream=True) as r:
        # write zip to temporary file and then read CSV from that temporary file
        content_disposition = r.headers.get('content-disposition', '')
        _, params = cgi.parse_header(content_disposition)
        file_name = params['filename']
        print(f'Reading ZIP \'{file_name}’ from web site...')
        with TemporaryFile() as temp_file:
            for chunk in r.iter_content(chunk_size=65536):
                temp_file.write(chunk)
            for p in patterns_from_zip(temp_file):
                yield p
            temp_file.seek(0, 0)
            with open(file_name, 'wb') as zip_file:
                while True:
                    chunk = temp_file.read(65536)
                    if not chunk:
                        break
                    zip_file.write(chunk)
            # for
        # with
    # with
    return


def patterns_from_file(zip_file_name) -> Generator[OrderedDict, None, None]:
    """
    Read a ZIP file from file system and yield patterns from 1st file (CSV) in that ZIP file
    :param zip_file_name:
    :return:
    """
    print(f'Reading number ranges from {zip_file_name}')
    with open(zip_file_name, 'rb') as f:
        for p in patterns_from_zip(f):
            yield p
        # for
    # with
    return


class Pattern:

    def __init__(self, p, start=None, end=None):
        if start is None:
            self.prefix = f'{p[" NIR"]}{p[" SERIE"]}'
            self.start = f"{int(p[' NUMERACION_INICIAL']):04d}"
            self.end = f"{int(p[' NUMERACION_FINAL']):04d}"
            self.summary = ''
        else:
            self.prefix, self.start, self.end = p, start, end
            self.summary = ''

        while self.end and self.end[-1] == '9' and self.start[-1] == '0':
            self.end = self.end[:-1]
            self.start = self.start[:-1]

    def __repr__(self):
        if self.start:
            return f'Pattern: {self.prefix} {self.start}-{self.end}'
        elif self.summary:
            return f'Pattern: {self.prefix}[{self.summary}]'
        else:
            return f'Pattern: {self.prefix}'

    def __lt__(self, other):
        return self.prefix < other.prefix or \
               (self.prefix == other.prefix and (self.start < other.start or
                                                 self.start == other.start and self.summary < other.summary))

    def __eq__(self, other):
        return self.prefix == other.prefix and self.start == other.start and self.end == other.end and \
               self.summary == other.summary

    def __gt__(self, other):
        return self.prefix > other.prefix or \
               (self.prefix == other.prefix and (self.start > other.start or
                                                 self.start == other.start and self.summary > other.summary))

    @property
    def for_ucm(self):
        """
        The pattern in the format to be used in UCM
        :return:
        """
        if self.start:
            r = None
        elif self.summary:
            r = f'{self.prefix}[{self.summary}]{"X" * (9 - len(self.prefix))}'
        else:
            r = f'{self.prefix}{"X" * (10 - len(self.prefix))}'
        return f'\\+52{r}'

    @property
    def covered_numbers(self):
        if self.start:
            r = None
        if self.summary:
            r = len(self.summary) * 10 ** (9 - len(self.prefix))
        else:
            r = 10 ** (10 - len(self.prefix))
        return r

    def expand(self) -> Generator['Pattern', None, None]:
        """
        Generator of "simple" patterns. A simple pattern does not have start nor end set
        :return:
        """
        if not self.start:
            # Already simple
            yield self
            return
        expanded = []
        # p 00-42 --> p00, p01, p02, p03, ..., p42
        digits = len(self.start)
        for i in range(int(self.start), int(self.end) + 1):
            expanded.append(Pattern(f'{self.prefix}{i:0{digits}}', start='', end=''))

        logging.debug(f'{self} expanded to {expanded}')
        for p in expanded:
            yield p
        return

    @staticmethod
    def expand_patterns(i: Iterable['Pattern']) -> Generator['Pattern', None, None]:
        """
        :param i: iterable
        :return:
        """
        for pattern in i:
            for p in pattern.expand():
                yield p
            # for
        # for
        return

    @staticmethod
    def summarize(i: Iterable['Pattern'], pattern_len) -> Generator['Pattern', None, None]:
        """
        :param i:
        :param pattern_len:
        :return:
        """
        prefix = None
        # add a marker at the end so that the summary active at the end is pushed through
        for pattern in chain(i, [Pattern('x' * pattern_len, '', '')]):
            if len(pattern.prefix) != pattern_len:
                # we don't care (yet)
                yield pattern
                continue
            if pattern.summary:
                # summary patterns can not be part of a summary
                yield pattern
                continue
            if prefix is None or pattern.prefix[:-1] != prefix:
                # this starts a new series of patterns to be summarized
                if prefix is not None:
                    # eject one summary pattern
                    if len(summary) == 1:
                        summary_pattern = Pattern(f'{prefix}{summary}', '', '')
                    else:
                        summary_pattern = Pattern(prefix, '', '')
                        if len(summary) != 10:
                            summary_pattern.summary = summary

                    if len(summarized) > 1:
                        logging.debug(f'{summary_pattern} as summary of: {", ".join((f"{p}" for p in summarized))}')
                    yield summary_pattern

                # start new prefix collection
                prefix = pattern.prefix[:-1]
                summary = pattern.prefix[-1]
                summarized = [pattern]
            else:
                summary += pattern.prefix[-1]
                summarized.append(pattern)
            # if .. else ..
        # for pattern ..
        return


def assert_partition(axl, name, read_only=True):
    """
    assert existence of partition w/ given name
    :param axl: AXL helper object
    :param name: partition name
    :param read_only: True=read only access to UCM
    :return: UUID of partition
    """
    p = axl.get_route_partition(name=name)
    if p is not None:
        r = p['uuid']
        print(f'Partition {name} exists.')
    else:
        print(f'Partition {name} does not exist.')
        if read_only:
            r = None
        else:
            r = axl.add_route_partition(name=name)
            print(f'Partition {name} created.')
    return r


def all_zips() -> List[str]:
    """
    Get a list of ZIP files in the current directory sorted from latest to oldest
    :return:
    """
    re_zip = re.compile(r'pnn_Publico_\d{2}_\d{2}_\d{4}.zip')
    zip_files = os.listdir()
    zip_files = [f for f in zip_files if re_zip.match(f) and os.path.isfile(f)]

    # sort files: why are the file names in dd_mm_yyyy? Just to make sorting harder?
    zip_files.sort(key=lambda x: f'{x[-8:-4]}{x[-11:-9]}{x[-14:-12]}', reverse=True)
    return zip_files


def optimize_patterns(patterns: Iterable) -> List[Pattern]:
    # we only want the mobile patterns
    patterns = [Pattern(p) for p in patterns if p[' TIPO_RED'] == 'MOVIL']

    print(f'got {len(patterns)} mobile patterns')

    print('sorting patterns...')
    patterns.sort()

    # consolidate mobile ranges
    print('expanding patterns...')
    patterns = [p for p in Pattern.expand_patterns(patterns)]
    print(f'expanded to {len(patterns)} patterns')

    for pattern_len in range(10, 2, -1):
        before = len(patterns)
        patterns.sort()
        patterns = [p for p in Pattern.summarize(patterns, pattern_len)]
        print(f'Summarize {pattern_len}: {before}->{len(patterns)}')
    return patterns


def list_compare(old: List, new: List) -> Tuple[List, List]:
    """
    Compare two sorted list and return tuple of two lists:
        1: deleted; entries in old but not in new
        2: addwed: entries in in new but not in old

    :param old: sorted list of entrires (the old ones)
    :param new: sorted list of entriues (the new ones)
    :return: tuple of two lists: deleted entries, added entries
    """
    added, deleted = [], []
    i_old, i_new = iter(old), iter(new)
    head_old, head_new = next(i_old, None), next(i_new, None)
    while True:
        if head_old is None and head_new is None:
            break
        if head_old is None:
            # the rest are new entries
            added.extend(e for e in i_new)
            break
        if head_new is None:
            # the rest was deleted
            deleted.extend(e for e in i_old)
            break
        if head_old == head_new:
            head_old, head_new = next(i_old, None), next(i_new, None)
        elif head_old > head_new:
            added.append(head_new)
            head_new = next(i_new, None)
        else:
            deleted.append(head_old)
            head_old = next(i_old, None)

    return deleted, added


def pattern_analysis(parsed_args):
    # read all CSVs
    all_patterns: List[Tuple[str, List[Pattern]]] = []
    for zip_name in all_zips():
        print(f'{zip_name}')
        patterns = patterns_from_zip(zip_name)
        patterns = optimize_patterns(patterns)
        all_patterns.append((zip_name, patterns))

    all_patterns.reverse()
    for i in range(len(all_patterns) - 1):
        old_name = all_patterns[i][0]
        new_name = all_patterns[i + 1][0]
        old_patterns = all_patterns[i][1]
        new_patterns = all_patterns[i + 1][1]

        print(f'{old_name} vs. {new_name}')
        patterns_deleted, patterns_added = list_compare(old_patterns, new_patterns)
        p: Pattern
        print(f'  {old_name}: {len(old_patterns)} patterns covering {sum(p.covered_numbers for p in old_patterns):,} numbers')
        print(f'  {new_name}: {len(new_patterns)} patterns covering {sum(p.covered_numbers for p in new_patterns):,} numbers')
        print(f'  {len(patterns_added)} patterns added')
        print(f'  {len(patterns_deleted)} patterns deleted')
        if parsed_args.patterns:
            changes: List[Tuple[Pattern, str]] = []
            changes.extend(((p, '  added') for p in patterns_added))
            changes.extend(((p, 'removed') for p in patterns_deleted))
            # sort on the 1st element of the tuple: the pattern
            changes.sort(key=lambda x: x[0])
            if changes:
                print('\n'.join((f'  {c[1]} {c[0].for_ucm}' for c in changes)))
        # if
    # for
    return


def provision_patterns(ucm, user, password, read_only, route_list_name, patterns):
    # provision blocking translation patterns or route patterns for optimized patterns

    # AXL helper object
    axl = ucmaxl.AXLHelper(ucm, auth=(user, password), version='10.0', verify=False,
                           timeout=60)

    # assert existence of partition
    local_partition = assert_partition(axl, PARTITION_NAME, read_only=read_only)

    if route_list_name is None:
        # provision blocking translation patterns
        lister = functools.partial(axl.list_translation, returned_tags=['pattern'])
        adder = functools.partial(axl.add_translation, partition=PARTITION_NAME,
                                  description=PARTITION_NAME,
                                  block_enable=True, urgency=True)
        remover = functools.partial(axl.remove_translation)
    else:
        # provision route patterns pointing to given route list

        # assert existence of route list
        route_list = axl.get_route_list(name=route_list_name)
        if route_list is None and not read_only:
            print(f'route list "{route_list_name}" needs to be created before executing the script')
            exit(2)
        # set methods for route patterns
        lister = functools.partial(axl.list_route_pattern, returned_tags=['pattern'])

        adder = functools.partial(axl.add_route_pattern,
                                  routePartitionName=PARTITION_NAME,
                                  digitDiscardInstructionName='PreDot',
                                  patternUrgency=True,
                                  blockEnable=False,
                                  destination={'routeListName': route_list_name},
                                  networkLocation='OffNet',
                                  description='Mobile number')
        remover = functools.partial(axl.remove_route_pattern)

    # get all patterns in given
    if local_partition is None:
        ucm_objects = []
    else:
        ucm_objects = lister(routePartitionName=PARTITION_NAME)

    print(f'{len(ucm_objects)} patterns exist in UCM')

    # determine patterns to be added/removed
    patterns = [p.for_ucm for p in patterns]
    ucm_patterns = [p['pattern'] for p in ucm_objects]

    new_patterns = [p for p in patterns if p not in ucm_patterns]
    print('{} new patterns need to be provisioned'.format(len(new_patterns)))

    remove_objects = [o for o in ucm_objects if o['pattern'] not in patterns]
    print('{} patterns need to be removed'.format(len(remove_objects)))

    # add new patterns
    print('adding patterns...')
    for pattern in tqdm(new_patterns):
        # print('Adding pattern {}'.format(pattern))
        if not read_only:
            adder(pattern=pattern)

    # remove patterns not needed any more
    print('removing patterns...')
    for pattern in tqdm(remove_objects):
        # print('Removing pattern {}'.format(pattern['pattern']))
        if not read_only:
            remover(uuid=pattern['uuid'])


def main():
    """
    :return:
    """
    # disable warnings for HTTPS sessions w/ diabled cert validation
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    args = argparse.ArgumentParser(
        description=f"""Provision blocking translation patterns or route patterns to cover all mobile phone number in 
        Mexico.
    The blocking translation patterns or route patterns are put into a '{PARTITION_NAME}' partition which is also 
    created if it doesn't 
    exist. If a route list is specified using --routelist then route patterns are provisioned pointing to that route 
    list.
    """)

    args.add_argument('--ucm', required=False,
                      help='IP or FQDN of UCM publisher host. If ucm is not given then only the patterns are printed')
    args.add_argument('--user', required=False, help='AXL user with write access to UCM')
    args.add_argument('--pwd', required=False, help='Password for AXL user with write access to UCM')
    args.add_argument('--fromfile', required=False,
                      help='name of ZIP file to read patterns from. If the file name is given as "." then we take the '
                           'latest pnn_Publico_??_??_????.zip')
    args.add_argument('--readonly', required=False, action='store_true',
                      help='Don\'t write to UCM. Existing patterns are read if possible.')
    args.add_argument('--routelist', required=False, help='provision route patterns pointing to given route list')
    args.add_argument('--analysis', required=False, action='store_true',
                      help='If present, then compare patterns of existing data sets')
    args.add_argument('--debug', required=False, action='store_true',
                      help='enable detailed debug messages to console')
    args.add_argument('--patterns', required=False, action='store_true',
                      help='dump resulting patterns to console')

    parsed_args = args.parse_args()

    if parsed_args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    if parsed_args.analysis:
        pattern_analysis(parsed_args=parsed_args)
        return

    if parsed_args.fromfile is not None:
        # we want to read from a zip file
        if parsed_args.fromfile == '.':
            # no name was given. Take the latest one
            zip_files = all_zips()
            parsed_args.fromfile = zip_files[0]
        patterns = patterns_from_file(parsed_args.fromfile)
    else:
        patterns = patterns_from_web()
    print('reading patterns...')

    patterns = optimize_patterns(patterns)

    if parsed_args.patterns:
        print('\n'.join((p.for_ucm for p in patterns)))
    print(f'summarized to {len(patterns)} patterns')

    if parsed_args.ucm is None:
        return

    provision_patterns(ucm=parsed_args.ucm, user=parsed_args.user, password=parsed_args.pwd,
                       read_only=parsed_args.readonly, route_list_name=parsed_args.routelist, patterns=patterns)
    return


if __name__ == '__main__':
    main()
