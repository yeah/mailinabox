#!/usr/bin/python3
import argparse
import datetime
import gzip
import os.path
import re
import shutil
import tempfile
import textwrap
from collections import defaultdict, OrderedDict

import dateutil.parser
import time

from dateutil.relativedelta import relativedelta

import utils


LOG_FILES = (
    '/var/log/mail.log',
    '/var/log/mail.log.1',
    '/var/log/mail.log.2.gz',
    '/var/log/mail.log.3.gz',
    '/var/log/mail.log.4.gz',
    '/var/log/mail.log.5.gz',
    '/var/log/mail.log.6.gz',
)

TIME_DELTAS = OrderedDict([
    ('all', datetime.timedelta(weeks=52)),
    ('month', datetime.timedelta(weeks=4)),
    ('2weeks', datetime.timedelta(days=14)),
    ('week', datetime.timedelta(days=7)),
    ('2days', datetime.timedelta(days=2)),
    ('day', datetime.timedelta(days=1)),
    ('12hours', datetime.timedelta(hours=12)),
    ('6hours', datetime.timedelta(hours=6)),
    ('hour', datetime.timedelta(hours=1)),
    ('30min', datetime.timedelta(minutes=30)),
    ('10min', datetime.timedelta(minutes=10)),
    ('5min', datetime.timedelta(minutes=5)),
    ('min', datetime.timedelta(minutes=1)),
    ('today', datetime.datetime.now() - datetime.datetime.now().replace(hour=0, minute=0, second=0))
])

# Start date > end date!
START_DATE = datetime.datetime.now()
END_DATE = None

VERBOSE = False

# List of strings to filter users with
FILTERS = None

# What to show by default
SCAN_OUT = True  # Outgoing email
SCAN_IN = True  # Incoming email
SCAN_CONN = False  # IMAP and POP3 logins
SCAN_GREY = False  # Greylisted email
SCAN_BLOCKED = False  # Rejected email


def scan_files(collector):
    """ Scan files until they run out or the earliest date is reached """

    stop_scan = False

    for fn in LOG_FILES:

        tmp_file = None

        if not os.path.exists(fn):
            continue
        elif fn[-3:] == '.gz':
            tmp_file = tempfile.NamedTemporaryFile()
            shutil.copyfileobj(gzip.open(fn), tmp_file)

        print("Processing file", fn, "...")
        fn = tmp_file.name if tmp_file else fn

        for line in reverse_readline(fn):
            if scan_mail_log_line(line.strip(), collector) is False:
                if stop_scan:
                    return
                stop_scan = True
            else:
                stop_scan = False



def scan_mail_log(env):
    """ Scan the system's mail log files and collect interesting data

    This function scans the 2 most recent mail log files in /var/log/.

    Args:
        env (dict): Dictionary containing MiaB settings

    """

    collector = {
        "scan_count": 0,  # Number of lines scanned
        "parse_count": 0,  # Number of lines parsed (i.e. that had their contents examined)
        "scan_time": time.time(),  # The time in seconds the scan took
        "sent_mail": OrderedDict(),  # Data about email sent by users
        "received_mail": OrderedDict(),  # Data about email received by users
        "dovecot": OrderedDict(),  # Data about Dovecot activity
        "postgrey": {},  # Data about greylisting of email addresses
        "rejected": OrderedDict(),  # Emails that were blocked
        "known_addresses": None,  # Addresses handled by the Miab installation
        "other-services": set(),
    }

    try:
        import mailconfig
        collector["known_addresses"] = (set(mailconfig.get_mail_users(env)) |
                                        set(alias[0] for alias in mailconfig.get_mail_aliases(env)))
    except ImportError:
        pass

    print("Scanning from {:%Y-%m-%d %H:%M:%S} back to {:%Y-%m-%d %H:%M:%S}".format(
        START_DATE, END_DATE)
    )

    # Scan the lines in the log files until the date goes out of range
    scan_files(collector)

    if not collector["scan_count"]:
        print("No log lines scanned...")
        return

    collector["scan_time"] = time.time() - collector["scan_time"]

    print("{scan_count} Log lines scanned, {parse_count} lines parsed in {scan_time:.2f} "
          "seconds\n".format(**collector))

    # Print Sent Mail report

    if collector["sent_mail"]:
        msg = "Sent email between {:%Y-%m-%d %H:%M:%S} and {:%Y-%m-%d %H:%M:%S}"
        print_header(msg.format(END_DATE, START_DATE))

        data = OrderedDict(sorted(collector["sent_mail"].items(), key=email_sort))

        print_user_table(
            data.keys(),
            data=[
                ("sent", [u["sent_count"] for u in data.values()]),
                ("hosts", [len(u["hosts"]) for u in data.values()]),
            ],
            sub_data=[
                ("sending hosts", [u["hosts"] for u in data.values()]),
            ],
            activity=[
                ("sent", [u["activity-by-hour"] for u in data.values()]),
            ],
            earliest=[u["earliest"] for u in data.values()],
            latest=[u["latest"] for u in data.values()],
        )

        accum = defaultdict(int)
        data = collector["sent_mail"].values()

        for h in range(24):
            accum[h] = sum(d["activity-by-hour"][h] for d in data)

        print_time_table(
            ["sent"],
            [accum]
        )

    # Print Received Mail report

    if collector["received_mail"]:
        msg = "Received email between {:%Y-%m-%d %H:%M:%S} and {:%Y-%m-%d %H:%M:%S}"
        print_header(msg.format(END_DATE, START_DATE))

        data = OrderedDict(sorted(collector["received_mail"].items(), key=email_sort))

        print_user_table(
            data.keys(),
            data=[
                ("received", [u["received_count"] for u in data.values()]),
            ],
            activity=[
                ("sent", [u["activity-by-hour"] for u in data.values()]),
            ],
            earliest=[u["earliest"] for u in data.values()],
            latest=[u["latest"] for u in data.values()],
        )

        accum = defaultdict(int)
        for h in range(24):
            accum[h] = sum(d["activity-by-hour"][h] for d in data.values())

        print_time_table(
            ["received"],
            [accum]
        )

    # Print Dovecot report

    if collector["dovecot"]:
        msg = "Email client logins between {:%Y-%m-%d %H:%M:%S} and {:%Y-%m-%d %H:%M:%S}"
        print_header(msg.format(END_DATE, START_DATE))

        data = OrderedDict(sorted(collector["dovecot"].items(), key=email_sort))

        print_user_table(
            data.keys(),
            data=[
                ("imap", [u["imap"] for u in data.values()]),
                ("pop3", [u["pop3"] for u in data.values()]),
            ],
            sub_data=[
                ("IMAP IP addresses", [[k + " (%d)" % v for k, v in u["imap-logins"].items()]
                                       for u in data.values()]),
                ("POP3 IP addresses", [[k + " (%d)" % v for k, v in u["pop3-logins"].items()]
                                       for u in data.values()]),
            ],
            activity=[
                ("imap", [u["activity-by-hour"]["imap"] for u in data.values()]),
                ("pop3", [u["activity-by-hour"]["pop3"] for u in data.values()]),
            ],
            earliest=[u["earliest"] for u in data.values()],
            latest=[u["latest"] for u in data.values()],
        )

        accum = {"imap": defaultdict(int), "pop3": defaultdict(int), "both": defaultdict(int)}
        for h in range(24):
            accum["imap"][h] = sum(d["activity-by-hour"]["imap"][h] for d in data.values())
            accum["pop3"][h] = sum(d["activity-by-hour"]["pop3"][h] for d in data.values())
            accum["both"][h] = accum["imap"][h] + accum["pop3"][h]

        print_time_table(
            ["imap", "pop3", "   +"],
            [accum["imap"], accum["pop3"], accum["both"]]
        )

    if collector["postgrey"]:
        msg = "Greylisted Email {:%Y-%m-%d %H:%M:%S} and {:%Y-%m-%d %H:%M:%S}"
        print_header(msg.format(END_DATE, START_DATE))

        print(textwrap.fill(
            "The following mail was greylisted, meaning the emails were temporarily rejected. "
            "Legitimate senders will try again within ten minutes.",
            width=80, initial_indent=" ", subsequent_indent=" "
        ), end='\n\n')

        data = OrderedDict(sorted(collector["postgrey"].items(), key=email_sort))
        users = []
        received = []
        senders = []
        sender_clients = []
        delivered_dates = []

        for recipient in data:
            sorted_recipients = sorted(data[recipient].items(), key=lambda kv: kv[1][0] or kv[1][1])
            for (client_address, sender), (first_date, delivered_date) in sorted_recipients:
                if first_date:
                    users.append(recipient)
                    received.append(first_date)
                    senders.append(sender)
                    delivered_dates.append(delivered_date)
                    sender_clients.append(client_address)

        print_user_table(
            users,
            data=[
                ("received", received),
                ("sender", senders),
                ("delivered", [str(d) or "no retry yet" for d in delivered_dates]),
                ("sending host", sender_clients)
            ],
            delimit=True,
        )

    if collector["rejected"]:
        msg = "Blocked Email {:%Y-%m-%d %H:%M:%S} and {:%Y-%m-%d %H:%M:%S}"
        print_header(msg.format(END_DATE, START_DATE))

        data = OrderedDict(sorted(collector["rejected"].items(), key=email_sort))

        rejects = []

        if VERBOSE:
            for user_data in data.values():
                user_rejects = []
                for date, sender, message in user_data["blocked"]:
                    if len(sender) > 64:
                        sender = sender[:32] + "…" + sender[-32:]
                    user_rejects.append("%s - %s " % (date, sender))
                    user_rejects.append("  %s" % message)
                rejects.append(user_rejects)

        print_user_table(
            data.keys(),
            data=[
                ("blocked", [len(u["blocked"]) for u in data.values()]),
            ],
            sub_data=[
                ("blocked emails", rejects),
            ],
            earliest=[u["earliest"] for u in data.values()],
            latest=[u["latest"] for u in data.values()],
        )

    if collector["other-services"] and VERBOSE and False:
        print_header("Other services")
        print("The following unkown services were found in the log file.")
        print(" ", *sorted(list(collector["other-services"])), sep='\n│ ')


def scan_mail_log_line(line, collector):
    """ Scan a log line and extract interesting data """

    m = re.match(r"(\w+[\s]+\d+ \d+:\d+:\d+) ([\w]+ )?([\w\-/]+)[^:]*: (.*)", line)

    if not m:
        return True

    date, system, service, log = m.groups()
    collector["scan_count"] += 1

    # print()
    # print("date:", date)
    # print("host:", system)
    # print("service:", service)
    # print("log:", log)

    # Replaced the dateutil parser for a less clever way of parser that is roughly 4 times faster.
    # date = dateutil.parser.parse(date)
    date = datetime.datetime.strptime(date, '%b %d %H:%M:%S')
    date = date.replace(START_DATE.year)

    # Check if the found date is within the time span we are scanning
    if date > START_DATE:
        # Don't process, but continue
        return True
    elif date < END_DATE:
        # Don't process, and halt
        return False

    if service == "postfix/submission/smtpd":
        if SCAN_OUT:
            scan_postfix_submission_line(date, log, collector)
    elif service == "postfix/lmtp":
        if SCAN_IN:
            scan_postfix_lmtp_line(date, log, collector)
    elif service in ("imap-login", "pop3-login"):
        if SCAN_CONN:
            scan_dovecot_line(date, log, collector, service[:4])
    elif service == "postgrey":
        if SCAN_GREY:
            scan_postgrey_line(date, log, collector)
    elif service == "postfix/smtpd":
        if SCAN_BLOCKED:
            scan_postfix_smtpd_line(date, log, collector)
    elif service in ("postfix/qmgr", "postfix/pickup", "postfix/cleanup", "postfix/scache",
                     "spampd", "postfix/anvil", "postfix/master", "opendkim", "postfix/lmtp",
                     "postfix/tlsmgr", "anvil"):
        # nothing to look at
        return True
    else:
        collector["other-services"].add(service)
        return True

    collector["parse_count"] += 1
    return True


def scan_postgrey_line(date, log, collector):
    """ Scan a postgrey log line and extract interesting data """

    m = re.match("action=(greylist|pass), reason=(.*?), (?:delay=\d+, )?client_name=(.*), "
                 "client_address=(.*), sender=(.*), recipient=(.*)",
                 log)

    if m:

        action, reason, client_name, client_address, sender, user = m.groups()

        if user_match(user):

            # Might be useful to group services that use a lot of mail different servers on sub
            # domains like <sub>1.domein.com

            # if '.' in client_name:
            #     addr = client_name.split('.')
            #     if len(addr) > 2:
            #         client_name = '.'.join(addr[1:])

            key = (client_address if client_name == 'unknown' else client_name, sender)

            rep = collector["postgrey"].setdefault(user, {})

            if action == "greylist" and reason == "new":
                rep[key] = (date, rep[key][1] if key in rep else None)
            elif action == "pass":
                rep[key] = (rep[key][0] if key in rep else None, date)


def scan_postfix_smtpd_line(date, log, collector):
    """ Scan a postfix smtpd log line and extract interesting data """

    # Check if the incoming mail was rejected

    m = re.match("NOQUEUE: reject: RCPT from .*?: (.*?); from=<(.*?)> to=<(.*?)>", log)

    if m:
        message, sender, user = m.groups()

        # skip this, if reported in the greylisting report
        if "Recipient address rejected: Greylisted" in message:
            return

        # only log mail to known recipients
        if user_match(user):
            if collector["known_addresses"] is None or user in collector["known_addresses"]:
                data = collector["rejected"].get(
                    user,
                    {
                        "blocked": [],
                        "earliest": None,
                        "latest": None,
                    }
                )
                # simplify this one
                m = re.search(
                    r"Client host \[(.*?)\] blocked using zen.spamhaus.org; (.*)", message
                )
                if m:
                    message = "ip blocked: " + m.group(2)
                else:
                    # simplify this one too
                    m = re.search(
                        r"Sender address \[.*@(.*)\] blocked using dbl.spamhaus.org; (.*)", message
                    )
                    if m:
                        message = "domain blocked: " + m.group(2)

                if data["latest"] is None:
                    data["latest"] = date
                data["earliest"] = date
                data["blocked"].append((date, sender, message))

                collector["rejected"][user] = data


def scan_dovecot_line(date, log, collector, prot):
    """ Scan a dovecot log line and extract interesting data """

    m = re.match("Info: Login: user=<(.*?)>, method=PLAIN, rip=(.*?),", log)

    if m:
        # TODO: CHECK DIT
        user, rip = m.groups()

        if user_match(user):
            # Get the user data, or create it if the user is new
            data = collector["dovecot"].get(
                user,
                {
                    "imap": 0,
                    "pop3": 0,
                    "earliest": None,
                    "latest": None,
                    "imap-logins": defaultdict(int),
                    "pop3-logins": defaultdict(int),
                    "activity-by-hour": {
                        "imap": defaultdict(int),
                        "pop3": defaultdict(int),
                    },
                }
            )

            data[prot] += 1
            data["activity-by-hour"][prot][date.hour] += 1

            if data["latest"] is None:
                data["latest"] = date
            data["earliest"] = date

            if rip not in ("127.0.0.1", "::1") or True:
                data["%s-logins" % prot][rip] += 1

            collector["dovecot"][user] = data


def scan_postfix_lmtp_line(date, log, collector):
    """ Scan a postfix lmtp log line and extract interesting data

    It is assumed that every log of postfix/lmtp indicates an email that was successfully
    received by Postfix.

    """

    m = re.match("([A-Z0-9]+): to=<(\S+)>, .* Saved", log)

    if m:
        _, user = m.groups()

        if user_match(user):
            # Get the user data, or create it if the user is new
            data = collector["received_mail"].get(
                user,
                {
                    "received_count": 0,
                    "earliest": None,
                    "latest": None,
                    "activity-by-hour": defaultdict(int),
                }
            )

            data["received_count"] += 1
            data["activity-by-hour"][date.hour] += 1

            if data["latest"] is None:
                data["latest"] = date
            data["earliest"] = date

            collector["received_mail"][user] = data


def scan_postfix_submission_line(date, log, collector):
    """ Scan a postfix submission log line and extract interesting data

    Lines containing a sasl_method with the values PLAIN or LOGIN are assumed to indicate a sent
    email.

    """

    # Match both the 'plain' and 'login' sasl methods, since both authentication methods are
    # allowed by Dovecot
    m = re.match("([A-Z0-9]+): client=(\S+), sasl_method=(PLAIN|LOGIN), sasl_username=(\S+)", log)

    if m:
        _, client, method, user = m.groups()

        if user_match(user):
            # Get the user data, or create it if the user is new
            data = collector["sent_mail"].get(
                user,
                {
                    "sent_count": 0,
                    "hosts": set(),
                    "earliest": None,
                    "latest": None,
                    "activity-by-hour": defaultdict(int),
                }
            )

            data["sent_count"] += 1
            data["hosts"].add(client)
            data["activity-by-hour"][date.hour] += 1

            if data["latest"] is None:
                data["latest"] = date
            data["earliest"] = date

            collector["sent_mail"][user] = data


# Utility functions

def reverse_readline(filename, buf_size=8192):
    """ A generator that returns the lines of a file in reverse order

    http://stackoverflow.com/a/23646049/801870

    """

    with open(filename) as fh:
        segment = None
        offset = 0
        fh.seek(0, os.SEEK_END)
        file_size = remaining_size = fh.tell()
        while remaining_size > 0:
            offset = min(file_size, offset + buf_size)
            fh.seek(file_size - offset)
            buff = fh.read(min(remaining_size, buf_size))
            remaining_size -= buf_size
            lines = buff.split('\n')
            # the first line of the buffer is probably not a complete line so
            # we'll save it and append it to the last line of the next buffer
            # we read
            if segment is not None:
                # if the previous chunk starts right from the beginning of line
                # do not concat the segment to the last line of new chunk
                # instead, yield the segment first
                if buff[-1] is not '\n':
                    lines[-1] += segment
                else:
                    yield segment
            segment = lines[0]
            for index in range(len(lines) - 1, 0, -1):
                if len(lines[index]):
                    yield lines[index]
        # Don't yield None if the file was empty
        if segment is not None:
            yield segment


def user_match(user):
    """ Check if the given user matches any of the filters """
    return FILTERS is None or any(u in user for u in FILTERS)


def email_sort(email):
    """ Split the given email address into a reverse order tuple, for sorting i.e (domain, name) """
    return tuple(reversed(email[0].split('@')))


def valid_date(string):
    """ Validate the given date string fetched from the --startdate argument """
    try:
        date = dateutil.parser.parse(string)
    except ValueError:
        raise argparse.ArgumentTypeError("Unrecognized date and/or time '%s'" % string)
    return date


# Print functions

def print_time_table(labels, data, do_print=True):
    labels.insert(0, "hour")
    data.insert(0, [str(h) for h in range(24)])

    temp = "│ {:<%d} " % max(len(l) for l in labels)
    lines = []

    for label in labels:
        lines.append(temp.format(label))

    for h in range(24):
        max_len = max(len(str(d[h])) for d in data)
        base = "{:>%d} " % max(2, max_len)

        for i, d in enumerate(data):
            lines[i] += base.format(d[h])

    lines.insert(0, "┬")
    lines.append("└" + (len(lines[-1]) - 2) * "─")

    if do_print:
        print("\n".join(lines))
    else:
        return lines


def print_user_table(users, data=None, sub_data=None, activity=None, latest=None, earliest=None,
                     delimit=False):
    str_temp = "{:<32} "
    lines = []
    data = data or []

    col_widths = len(data) * [0]
    col_left = len(data) * [False]
    vert_pos = 0

    do_accum = all(isinstance(n, (int, float)) for _, d in data for n in d)
    data_accum = len(data) * ([0] if do_accum else [" "])

    last_user = None

    for row, user in enumerate(users):

        if delimit:
            if last_user and last_user != user:
                lines.append(len(lines[-1]) * "…")
            last_user = user

        line = "{:<32} ".format(user[:31] + "…" if len(user) > 32 else user)

        for col, (l, d) in enumerate(data):
            if isinstance(d[row], str):
                col_str = str_temp.format(d[row][:31] + "…" if len(d[row]) > 32 else d[row])
                col_left[col] = True
            elif isinstance(d[row], datetime.datetime):
                col_str = "{:<20}".format(str(d[row]))
                col_left[col] = True
            else:
                temp = "{:>%s}" % max(5, len(l) + 1, len(str(d[row])) + 1)
                col_str = temp.format(str(d[row]))
            col_widths[col] = max(col_widths[col], len(col_str))
            line += col_str

            if do_accum:
                data_accum[col] += d[row]

        try:
            if None not in [latest, earliest]:
                vert_pos = len(line)
                e = earliest[row]
                l = latest[row]
                timespan = relativedelta(l, e)
                if timespan.months:
                    temp = " │ {:0.1f} months"
                    line += temp.format(timespan.months + timespan.days / 30.0)
                elif timespan.days:
                    temp = " │ {:0.1f} days"
                    line += temp.format(timespan.days + timespan.hours / 24.0)
                elif (e.hour, e.minute) == (l.hour, l.minute):
                    temp = " │ {:%H:%M}"
                    line += temp.format(e)
                else:
                    temp = " │ {:%H:%M} - {:%H:%M}"
                    line += temp.format(e, l)

        except KeyError:
            pass

        lines.append(line.rstrip())

        try:
            if VERBOSE:
                if sub_data is not None:
                    for l, d in sub_data:
                        if d[row]:
                            lines.append("┬")
                            lines.append("│ %s" % l)
                            lines.append("├─%s─" % (len(l) * "─"))
                            lines.append("│")
                            max_len = 0
                            for v in list(d[row]):
                                lines.append("│ %s" % v)
                                max_len = max(max_len, len(v))
                            lines.append("└" + (max_len + 1) * "─")

                if activity is not None:
                    lines.extend(print_time_table(
                        [label for label, _ in activity],
                        [data[row] for _, data in activity],
                        do_print=False
                    ))

        except KeyError:
            pass

    header = str_temp.format("")

    for col, (l, _) in enumerate(data):
        if col_left[col]:
            header += l.ljust(max(5, len(l) + 1, col_widths[col]))
        else:
            header += l.rjust(max(5, len(l) + 1, col_widths[col]))

    if None not in (latest, earliest):
        header += " │ timespan   "

    lines.insert(0, header.rstrip())

    table_width = max(len(l) for l in lines)
    t_line = table_width * "─"
    b_line = table_width * "─"

    if vert_pos:
        t_line = t_line[:vert_pos + 1] + "┼" + t_line[vert_pos + 2:]
        b_line = b_line[:vert_pos + 1] + ("┬" if VERBOSE else "┼") + b_line[vert_pos + 2:]

    lines.insert(1, t_line)
    lines.append(b_line)

    # Print totals

    data_accum = [str(a) for a in data_accum]
    footer = str_temp.format("Totals:" if do_accum else " ")
    for row, (l, _) in enumerate(data):
        temp = "{:>%d}" % max(5, len(l) + 1)
        footer += temp.format(data_accum[row])

    try:
        if None not in [latest, earliest]:
            max_l = max(latest)
            min_e = min(earliest)
            timespan = relativedelta(max_l, min_e)
            if timespan.days:
                temp = " │ {:0.2f} days"
                footer += temp.format(timespan.days + timespan.hours / 24.0)
            elif (min_e.hour, min_e.minute) == (max_l.hour, max_l.minute):
                temp = " │ {:%H:%M}"
                footer += temp.format(min_e)
            else:
                temp = " │ {:%H:%M} - {:%H:%M}"
                footer += temp.format(min_e, max_l)

    except KeyError:
        pass

    lines.append(footer)

    print("\n".join(lines))


def print_header(msg):
    print('\n' + msg)
    print("═" * len(msg), '\n')


if __name__ == "__main__":
    try:
        env_vars = utils.load_environment()
    except FileNotFoundError:
        env_vars = {}

    parser = argparse.ArgumentParser(
        description="Scan the mail log files for interesting data. By default, this script "
                    "shows today's incoming and outgoing mail statistics. This script was ("
                    "re)written for the Mail-in-a-box email server."
                    "https://github.com/mail-in-a-box/mailinabox",
        add_help=False
    )

    # Switches to determine what to parse and what to ignore

    parser.add_argument("-r", "--received", help="Scan for received emails.",
                        action="store_true")
    parser.add_argument("-s", "--sent", help="Scan for sent emails.",
                        action="store_true")
    parser.add_argument("-l", "--logins", help="Scan for IMAP/POP logins.",
                        action="store_true")
    parser.add_argument("-g", "--grey", help="Scan for greylisted emails.",
                        action="store_true")
    parser.add_argument("-b", "--blocked", help="Scan for blocked emails.",
                        action="store_true")

    parser.add_argument("-t", "--timespan", choices=TIME_DELTAS.keys(), default='today',
                        metavar='<time span>',
                        help="Time span to scan, going back from the start date. Possible values: "
                             "{}. Defaults to 'today'.".format(", ".join(list(TIME_DELTAS.keys()))))
    parser.add_argument("-d", "--startdate",  action="store", dest="startdate",
                        type=valid_date, metavar='<start date>',
                        help="Date and time to start scanning the log file from. If no date is "
                             "provided, scanning will start from the current date and time.")
    parser.add_argument("-u", "--users", action="store", dest="users",
                        metavar='<email1,email2,email...>',
                        help="Comma separated list of (partial) email addresses to filter the "
                             "output with.")

    parser.add_argument('-h', '--help', action='help', help="Print this message and exit.")
    parser.add_argument("-v", "--verbose", help="Output extra data where available.",
                        action="store_true")

    args = parser.parse_args()

    if args.startdate is not None:
        START_DATE = args.startdate
        if args.timespan == 'today':
            args.timespan = 'day'
        print("Setting start date to {}".format(START_DATE))

    END_DATE = START_DATE - TIME_DELTAS[args.timespan]

    VERBOSE = args.verbose

    if args.received or args.sent or args.logins or args.grey or args.blocked:
        SCAN_IN = args.received
        if not SCAN_IN:
            print("Ignoring received emails")

        SCAN_OUT = args.sent
        if not SCAN_OUT:
            print("Ignoring sent emails")

        SCAN_CONN = args.logins
        if not SCAN_CONN:
            print("Ignoring logins")

        SCAN_GREY = args.grey
        if SCAN_GREY:
            print("Showing greylisted emails")

        SCAN_BLOCKED = args.blocked
        if SCAN_BLOCKED:
            print("Showing blocked emails")

    if args.users is not None:
        FILTERS = args.users.strip().split(',')

    scan_mail_log(env_vars)
