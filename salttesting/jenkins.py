# -*- coding: utf-8 -*-
'''
    :codeauthor: :email:`Pedro Algarvio (pedro@algarvio.me)`


    salttesting.jenkins
    ~~~~~~~~~~~~~~~~~~~

    Jenkins execution helper script
'''

# Import python libs
import os
import sys
import json
import time
import pipes
import random
import shutil
import hashlib
import argparse
import subprocess

# Import salt libs
from salt.utils import vt, get_colors

# Import salt-testing libs
from salttesting.runtests import print_header

# Import 3rd-party libs
import yaml
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

SALT_GIT_URL = 'https://github.com/saltstack/salt.git'


# ----- Argparse Custom Actions ------------------------------------------------------------------------------------->
class GetPullRequestAction(argparse.Action):
    '''
    Load the required pull request information
    '''

    def __call__(self, parser, namespace, values, option_string=None):
        if HAS_REQUESTS is False:
            parser.error(
                'The python \'requests\' library needs to be installed'
            )

        headers = {}
        url = 'https://api.github.com/repos/saltstack/salt/pulls/{0}'.format(values)

        github_access_token_path = os.path.join(
            os.environ.get('JENKINS_HOME', os.path.expanduser('~')),
            '.github_token'
        )
        if os.path.isfile(github_access_token_path):
            headers = {
                'Authorization': 'token {0}'.format(
                    open(github_access_token_path).read().strip()
                )
            }

        http_req = requests.get(url, headers=headers)
        if http_req.status_code != 200:
            parser.error(
                'Unable to get the pull request: {0[message]}'.format(http_req.json())
            )

        pr_details = http_req.json()
        setattr(namespace, 'pull_request_git_url', pr_details['head']['repo']['clone_url'])
        setattr(namespace, 'pull_request_git_commit', pr_details['head']['sha'])
# <---- Argparse Custom Actions --------------------------------------------------------------------------------------

# ----- Helper Functions -------------------------------------------------------------------------------------------->
def generate_ssh_keypair(options):
    '''
    Generate a temporary SSH key, valid for one hour, and set it as an
    authorized key in the minion's root user account on the remote system.
    '''
    print('Generating temporary SSH Key')
    ssh_key_path = os.path.join(options.workspace, 'jenkins_test_account_key')

    if os.path.exists(ssh_key_path):
        os.unlink(ssh_key_path)
        os.unlink(ssh_key_path + '.pub')

    exitcode = run_command(
        'ssh-keygen -t ecdsa -b 521 -C "$(whoami)@$(hostname)-$(date --rfc-3339=seconds)" '
        '-f {0} -N \'\' -V -10m:+2h'.format(ssh_key_path)
    )
    if exitcode != 0:
        exitcode = run_command(
            'ssh-keygen -t rsa -b 2048 -C "$(whoami)@$(hostname)-$(date --rfc-3339=seconds)" '
            '-f {0} -N \'\' -V -10m:+2h'.format(ssh_key_path)
        )
        if exitcode != 0:
            print('Failed to generate temporary SSH ksys')
            sys.exit(1)

def generate_vm_name(options):
    '''
    Generate a random enough vm name
    '''
    if 'BUILD_NUMBER' in os.environ:
        random_part = 'build{0:0>6}'.format(os.environ.get('BUILD_NUMBER'))
    else:
        random_part = hashlib.md5(
            str(random.randint(1, 100000000))).hexdigest()[:6]

    return '{0}-{1}-{2}'.format(options.vm_prefix, options.vm_source.split('_')[-1], random_part)


def get_vm_name(options):
    '''
    Return the VM name
    '''
    return os.environ.get('JENKINS_VM_NAME', generate_vm_name(options))


def to_cli_yaml(data):
    '''
    Return a YAML string for CLI usage
    '''
    return yaml.dump(data, default_flow_style=True, indent=0, width=sys.maxint).rstrip()


def build_pillar_data(options):
    '''
    Build a YAML formatted string to properly pass pillar data
    '''
    pillar = {'test_transport': options.test_transport}
    if options.test_git_commit is not None:
        pillar['test_git_commit'] = options.test_git_commit
    if options.test_git_url is not None:
        pillar['test_git_url'] = options.test_git_url
    if options.bootstrap_salt_url is not None:
        pillar['bootstrap_salt_url'] = options.bootstrap_salt_url
    if options.bootstrap_salt_commit is not None:
        pillar['bootstrap_salt_commit'] = options.bootstrap_salt_commit
    if options.test_pillar:
        pillar.update(dict(options.test_pillar))
    return to_cli_yaml(pillar)


def echo_parseable_environment(options):
    '''
    Echo NAME=VAL parseable output
    '''
    output = [
        'JENKINS_VM_NAME={0}'.format(options.vm_name),
        'JENKINS_VM_SOURCE={0}'.format(options.vm_source),
    ]
    if 'pull_request_git_url' in options and 'pull_request_git_commit' in options:
        output.extend([
            'SALT_PR_GIT_URL={0}'.format(options.pull_request_git_url),
            'SALT_PR_GIT_COMMIT={0}'.format(options.pull_request_git_commit)
        ])

    sys.stdout.write('\n\n{0}\n\n'.format('\n'.join(output)))
    sys.stdout.flush()


def run_command(cmd, sleep=0.015, return_output=False):
    '''
    Run a command using VT
    '''
    print_header(u'', sep='>', inline=True)
    if isinstance(cmd, list):
        cmd = ' '.join(cmd)

    print('Running command: {0}'.format(cmd))
    print_header(u'', sep='-', inline=True)

    if return_output is True:
        stdout_buffer = stderr_buffer = ''

    try:
        proc = vt.Terminal(
            cmd,
            shell=True,
            stream_stdout=True,
            stream_stderr=True
        )

        proc_terminated = False
        while True:
            stdout, stderr = proc.recv()
            if return_output is True:
                stdout_buffer += stdout or ''
                stderr_buffer += stderr or ''

            if proc_terminated:
                break

            if not proc.isalive():
                proc_terminated = True

            time.sleep(sleep)
        if proc.exitstatus != 0:
            print_header(u'', sep='-', inline=True)
            print('Failed execute command. Exit code: {0}'.format(proc.exitstatus))
        else:
            print_header(u'', sep='-', inline=True)
            print('Command execution succeeded. Exit code: {0}'.format(proc.exitstatus))
        if return_output is True:
            return stdout_buffer, stderr_buffer, proc.exitstatus
        return proc.exitstatus
    except vt.TerminalException as exc:
        print_header(u'', sep='-', inline=True)
        print('\n\nAn error occurred while running command:\n')
        print(str(exc))
    finally:
        print_header(u'', sep='<', inline=True)
        proc.close(terminate=True, kill=True)


def bootstrap_cloud_minion(options):
    '''
    Bootstrap a minion using salt-cloud
    '''
    cmd = ['salt-cloud', '-l', 'info']
    script_args = ['-D']
    if options.no_color:
        script_args.append('-n')
    if options.bootstrap_salt_url != SALT_GIT_URL:
        script_args.extend([
            '-g', options.bootstrap_salt_url
        ])
    script_args.extend(['git', options.bootstrap_salt_commit])

    cmd = [
        'salt-cloud',
        '-l', 'debug',
        '--script-args="{0}"'.format(' '.join(script_args)),
        '-p', options.vm_source,
        options.vm_name
    ]
    if options.no_color:
        cmd.append('--no-color')

    return run_command(cmd)


def bootstrap_lxc_minion(options):
    '''
    Bootstrap a minion using salt-cloud
    '''

    print('LXC support not implemented')
    sys.exit(1)

    cmd = ['salt-cloud', '-l', 'debug']
    script_args = ['-D']
    if options.no_color:
        script_args.append('-n')
    if options.bootstrap_salt_url != SALT_GIT_URL:
        script_args.extend([
            '-g', options.bootstrap_salt_url
        ])
    script_args.extend(['git', options.bootstrap_salt_commit])

    cmd = [
        'salt-cloud',
        '-l', 'debug',
        '--script-args="{0}"'.format(' '.join(script_args)),
        '-p', options.vm_source,
        options.vm_name
    ]
    if options.no_color:
        cmd.append('--no-color')

    return run_command(cmd)


def prepare_ssh_access(options):
    print('Prepare SSH Access to Bootstrapped VM')
    generate_ssh_keypair(options)

    cmd = [
        'salt',
        '-t', '100',
        options.vm_name,
        'state.sls',
        options.ssh_prepare_state,
        'pillar="{0}"'.format(
            to_cli_yaml({
                'test_username': options.ssh_username,
                'test_pubkey': open(
                    os.path.join(options.workspace, 'jenkins_test_account_key.pub')
                ).read().strip()
            })
        )
    ]
    if options.no_color:
        cmd.append('--no-color')

    return run_command(cmd)


def sync_minion(options):
    if 'salt_minion_synced' in options:
        return

    exitcode = run_command('salt -t 100 {0} saltutil.sync_all'.format(options.vm_name))
    setattr(options, 'salt_minion_synced', 'yes')
    return exitcode


def get_minion_external_address(options):
    '''
    Get and store the remote minion external IP
    '''
    if 'minion_external_ip' in options:
        return options.minion_external_ip

    sync_minion(options)

    stdout_buffer = stderr_buffer = ''
    cmd = 'salt --out=json {0} grains.get external_ip'.format(options.vm_name)
    stdout, stderr, exitcode = run_command(cmd, return_output=True)
    if exitcode != 0:
        print('Failed to get the minion external IP. Exit code: {0}'.format(exitcode))
        sys.exit(exitcode)

    if not stdout.strip():
        print('Failed to get the minion external IP(no output)')
        sys.stdout.flush()
        sys.exit(1)

    try:
        external_ip_info = json.loads(stdout.strip())
        external_ip = external_ip_info[options.vm_name]
    except ValueError:
        print('Failed to load any JSON from {0!r}'.format(stdout.strip()))
    setattr(options, 'minion_external_ip', external_ip)
    return external_ip


def get_minion_python_executable(options):
    '''
    Get and store the remote minion python executable
    '''
    if 'minion_python_executable' in options:
        return options.minion_python_executable

    sync_minion(options)

    cmd = 'salt --out=json {0} grains.get pythonexecutable'.format(options.vm_name)
    stdout, stderr, exitcode = run_command(cmd, return_output=True)
    if exitcode != 0:
        print('Failed to get the minion python executable. Exit code: {0}'.format(exitcode))
        sys.exit(exitcode)

    if not stdout.strip():
        print('Failed to get the minion external IP(no output)')
        sys.stdout.flush()
        sys.exit(1)

    try:
        python_executable = json.loads(stdout.strip())
        python_executable = python_executable[options.vm_name]
    except ValueError:
        print('Failed to load any JSON from {0!r}'.format(stdout.strip()))

    setattr(options, 'minion_python_executable', python_executable)
    return python_executable


def delete_cloud_vm(options):
    '''
    Delete a salt-cloud instance
    '''
    cmd = ['salt-cloud', '-yd']
    if options.no_color:
        cmd.append('--no-color')
    cmd.append(options.vm_name)
    return run_command(cmd)


def delete_lxc_vm(options):
    '''
    Delete an lxc instance
    '''
    cmd = ['salt-run']
    if options.no_color:
        cmd.append('--no-color')
    cmd.append('lxc.purge')
    cmd.append(options.vm_name)

    return run_command(cmd)


def check_boostrapped_minion_version(options):
    '''
    Confirm that the bootstrapped minion version matches the desired one
    '''
    print('Grabbing bootstrapped minion version information ... ')
    cmd = 'salt -t 100 {0} --out json test.version'.format(options.vm_name)

    stdout, stderr, exitcode = run_command(cmd, return_output=True)
    if exitcode:
        print('Failed to get the bootstrapped minion version. Exit code: {0}'.format(exitcode))
        sys.exit(exitcode)

    if not stdout.strip():
        print('Failed to get the bootstrapped minion version(no output).')
        sys.stdout.flush()
        sys.exit(1)

    try:
        version_info = json.loads(stdout.strip())
        bootstrap_minion_version = os.environ.get(
            'SALT_MINION_BOOTSTRAP_RELEASE',
            options.bootstrap_salt_commit[:7]
        )
        if bootstrap_minion_version not in version_info[options.vm_name]:
            print('\n\nATTENTION!!!!\n')
            print('The boostrapped minion version commit does not contain the desired commit:')
            print(' {0!r} does not contain {1!r}'.format(version_info[options.vm_name], bootstrap_minion_version))
            print('\n\n')
            sys.stdout.flush()
        else:
            print('matches!')
    except ValueError:
        print('Failed to load any JSON from {0!r}'.format(stdout.strip()))


def run_state_on_vm(options, state_name, timeout=100):
    '''
    Run a state on the VM
    '''
    test_ssh_root_login(options)
    cmd = [
        'salt-call',
        '--timeout={0}'.format(timeout),
        '--retcode-passthrough',
        'state.sls',
        state_name,
        'pillar="{0}"'.format(build_pillar_data(options))
    ]
    if options.require_sudo:
        cmd.insert(0, 'sudo')
    if options.no_color:
        cmd.append('--no-color')

    return run_ssh_command(options, cmd)


def download_unittest_reports(options):
    print('Downloading remote unittest reports...')
    sys.stdout.flush()

    workspace = options.workspace
    xml_reports_path = os.path.join(workspace, 'xml-test-reports')
    if os.path.isdir(xml_reports_path):
        shutil.rmtree(xml_reports_path)

    os.makedirs(xml_reports_path)


def build_ssh_opts(options):
    '''
    Return a list of SSH options
    '''
    ssh_args = [
        # Don't add new hosts to the host key database
        '-oStrictHostKeyChecking=no',
        # Set hosts key database path to /dev/null, ie, non-existing
        '-oUserKnownHostsFile=/dev/null',
        # Don't re-use the SSH connection. Less failures.
        '-oControlPath=none',
        # tell SSH to skip password authentication
        '-oPasswordAuthentication=no',
        '-oChallengeResponseAuthentication=no',
        # Make sure public key authentication is enabled
        '-oPubkeyAuthentication=yes',
        # No Keyboard interaction!
        '-oKbdInteractiveAuthentication=no',
        # Also, specify the location of the key file
        '-oIdentityFile={0}'.format(
            os.path.join(options.workspace, 'jenkins_test_account_key')
        ),
        # Use double `-t` on the `ssh` command, it's necessary when `sudo` has
        # `requiretty` enforced.
        '-t', '-t',
    ]
    return ssh_args


def run_ssh_command(options, remote_command):
    '''
    Run a command using SSH
    '''
    test_ssh_root_login(options)
    cmd = ['ssh'] + build_ssh_opts(options)
    cmd.append(
        '{0}@{1}'.format(
            options.require_sudo and options.ssh_username or 'root',
            get_minion_external_address(options)
        )
    )
    if isinstance(remote_command, (list, tuple)):
        remote_command = ' '.join(remote_command)
    if options.require_sudo and not remote_command.startswith('sudo'):
        remote_command = 'sudo {0}'.format(remote_command)
    cmd.append(pipes.quote(remote_command))
    return run_command(cmd)


def test_ssh_root_login(options):
    '''
    Test if we're able to login as root
    '''
    if 'require_sudo' in options:
        return

    cmd = ['ssh'] + build_ssh_opts(options)
    cmd.extend([
        'root@{0}'.format(get_minion_external_address(options)),
        'echo "root login possible"'
    ])
    exitcode = run_command(cmd)
    setattr(options, 'require_sudo', exitcode != 0)


def download_artifacts(options):
    artifacts = []
    for remote_path, local_path in options.download_artifact:
        if not os.path.isdir(local_path):
            os.makedirs(local_path)
        artifacts.append((
            remote_path,
            os.path.join(local_path, os.path.basename(remote_path))
        ))
    sftp_command = ['sftp'] + build_ssh_opts
    sftp_command.append(
        '{0}@{1}'.format(
            options.require_sudo and options.ssh_username or 'root',
            get_minion_external_address(options)
        )
    )
    for remote_path, local_path in artifacts:
        run_command(
            'echo "get {0} {1}" | {2}'.format(
                remote_path,
                local_path,
                ' '.join(sftp_command)
            )
        )
# <---- Helper Functions ---------------------------------------------------------------------------------------------

# ----- Parser Code ------------------------------------------------------------------------------------------------->
def main():
    parser = argparse.ArgumentParser(description='Jenkins execution helper')
    parser.add_argument(
        '-w', '--workspace',
        default=os.path.abspath(os.environ.get('WORKSPACE', os.getcwd())),
        help=('Path to the execution workspace. Defaults to the \'WORKSPACE\' environment '
              'variable or the current directory.')
    )

    # Output Options
    output_group = parser.add_argument_group('Output Options')
    output_group.add_argument(
        '--no-color',
        '--no-colour',
        action='store_true',
        default=False,
        help='Don\'t use colors'
    )
    output_group.add_argument(
        '--echo-parseable-output',
        action='store_true',
        default=False,
        help='Print Jenkins related environment variables and exit'
    )
    output_group.add_argument(
        '--pull-request',
        type=int,
        action=GetPullRequestAction,
        default=None,
        help='Include the Pull Request information in parseable output'
    )

    # SSH Options
    ssh_options_group = parser.add_argument_group(
        'SSH Option(s)',
        'These SSH option(s) are used on all SSH related communications'
        '(except when initially bootstrapping the minion)'
    )
    ssh_options_group.add_argument(
        '--ssh-username',
        default='test-account',
        help='The username to use in all SSH related communications'
    )
    ssh_options_group.add_argument(
        '--ssh-prepare-state',
        default='accounts.test_account',
        help='The name of the state which prepares the remove VM for SSH access'
    )

    # Deployment Selection
    deployment_group = parser.add_argument_group('Deployment Selection')
    deployment_group_mutually_exclusive = deployment_group.add_mutually_exclusive_group()
    deployment_group_mutually_exclusive.add_argument(
        '--cloud-deploy',
        action='store_true',
        default=False,
        help='Salt Cloud Deployment. The default deployment.'
    )
    deployment_group_mutually_exclusive.add_argument(
        '--lxc-deploy',
        action='store_true',
        default=False,
        help='Salt LXC Deployment'
    )
    deployment_group.add_argument(
        '--lxc-host',
        default=None,
        help='The host where to deploy the LXC VM'
    )

    # Bootstrap Script Options
    bootstrap_script_options = parser.add_argument_group(
        'Bootstrap Script Options',
        'In case there\'s a need to provide the bootstrap script from an '
        'alternate URL and/or from a specific commit.'
    )
    bootstrap_script_options.add_argument(
        '--bootstrap-salt-url',
        default=None,
        help='The salt git repository url used to bootstrap a minion'
    )
    bootstrap_script_options.add_argument(
        '--bootstrap-salt-commit',
        default=None,
        help='The salt git commit used to bootstrap a minion'
    )

    # VM related options
    vm_options_group = parser.add_argument_group('VM Options')
    vm_options_group.add_argument('vm_name', nargs='?', help='Virtual machine name')
    vm_options_group.add_argument(
        '--vm-prefix',
        default=os.environ.get('JENKINS_VM_NAME_PREFIX', 'zjenkins'),
        help='The bootstrapped machine name prefix. Default: %(default)r'
    )
    vm_options_group.add_argument(
        '--vm-source',
        default=os.environ.get('JENKINS_VM_SOURCE', None),
        help=('The VM source. In case of --cloud-deploy usage, the could profile name. '
              'In case of --lxc-deploy usage, the image name.')
    )

    # VM related actions
    vm_actions = parser.add_argument_group(
        'VM Actions',
        'Action to execute on a running VM'
    )
    vm_actions.add_argument(
        '--delete-vm',
        action='store_true',
        default=False,
        help='Delete a running VM'
    )
    vm_actions.add_argument(
        '--download-artifact',
        default=[],
        nargs=2,
        action='append',
        metavar=('REMOTE_PATH', 'LOCAL_PATH'),
        help='Download remote artifacts.'
    )

    testing_source_options = parser.add_argument_group(
        'Testing Options',
        'In case there\'s a need to provide a different repository and/or commit from which '
        'the tests suite should be executed on'
    )
    testing_source_options.add_argument(
        '--test-transport',
        default='zeromq',
        choices=('zeromq', 'raet'),
        help='Set to raet to run integration tests with raet transport. Default: %(default)s')
    testing_source_options.add_argument(
        '--test-git-url',
        default=None,
        help='The testing git repository url')
    testing_source_options.add_argument(
        '--test-git-commit',
        default=None,
        help='The testing git commit to track')
    testing_source_options.add_argument(
        '--test-pillar',
        default=[],
        nargs=2,
        metavar=('PILLAR_KEY', 'PILLAR_VALUE'),
        help=('Additional pillar data use in the build. Pass a key and a value per '
              '\'--test-pillar\' option. Example: --test-pillar foo_key foo_value')
    )
    testing_source_options.add_argument(
        '--test-prep-sls',
        default=[],
        action='append',
        help='Run a preparation SLS file. Pass one SLS per `--test-prep-sls` option argument'
    )
    testing_source_options_mutually_exclusive = testing_source_options.add_mutually_exclusive_group()
    testing_source_options_mutually_exclusive.add_argument(
        '--test-command',
        default=None,
        help='The command to execute on the deployed VM to run tests'
    )
    testing_source_options_mutually_exclusive.add_argument(
        '--test-default-command',
        action='store_true',
        help=('Run the default salt runtests command: '
              '\'{python_executable} /testing/tests/runtests.py -v --run-destructive --sysinfo '
              '{no_color} --xml=/tmp/xml-unitests-output --coverage-xml=/tmp/coverage.xml '
              '--transport={transport}\'')
    )

    packaging_options = parser.add_argument_group(
        'Packaging Options',
        'Remove build of packages options'
    )
    packaging_options.add_argument(
        '--build-packages',
        default=True,
        action='store_true',
        help='Run buildpackage.py to create packages off of the git build.'
    )
    # These next three options are ignored if --build-packages is False
    packaging_options.add_argument(
        '--package-source-dir',
        default='/testing',
        help='Directory where the salt source code checkout is found '
             '(default: %(default)s)',
    )
    packaging_options.add_argument(
        '--package-build-dir',
        default='/tmp/salt-buildpackage',
        help='Build root for automated package builds (default: %(default)s)',
    )
    packaging_options.add_argument(
        '--package-artifact-dir',
        default='/tmp/salt-packages',
        help='Location on the minion from which packages should be '
             'retrieved (default: %(default)s)',
    )

    options = parser.parse_args()

    if options.lxc_deploy or options.lxc_host:
        parser.error('LXC support is not yet implemented')

    if options.vm_name is None:
        options.vm_name = get_vm_name(options)

    if options.echo_parseable_output:
        if not options.vm_source:
            parser.error('--vm-source is required in order to print out the required Jenkins variables')
        echo_parseable_environment(options)
        parser.exit(0)

    if options.delete_vm:
        if options.cloud_deploy:
            parser.exit(delete_cloud_vm(options))
        elif options.lxc_deploy:
            parser.exit(delete_lxc_vm(options))
        else:
            parser.error(
                'You need to specify from which deployment to delete the VM from. --cloud-deploy/--lxc-deploy'
            )

    if options.bootstrap_salt_commit is None:
        options.bootstrap_salt_commit = os.environ.get(
            'SALT_MINION_BOOTSTRAP_RELEASE', 'develop'
        )

    if options.bootstrap_salt_url is None:
        options.bootstrap_salt_url = SALT_GIT_URL

    if options.cloud_deploy:
        exitcode = bootstrap_cloud_minion(options)
        if exitcode != 0:
            print('Failed to bootstrap the cloud minion')
            parser.exit(exitcode)
    elif options.lxc_deploy:
        exitcode = bootstrap_lxc_minion(options)
        if exitcode != 0:
            print('Failed to bootstrap the LXC minion')
            parser.exit(exitcode)

    if options.cloud_deploy or options.lxc_deploy:
        check_boostrapped_minion_version(options)
        prepare_ssh_access(options)

    # Run preparation SLS
    for sls in options.test_prep_sls:
        exitcode = run_state_on_vm(options, sls, timeout=900)
        if exitcode != 0:
            print('The execution of the {0!r} SLS failed')
            parser.exit(exitcode)

    # Run the main command using SSH for realtime output
    if options.test_default_command:
        options.test_command = (
            '{python_executable} /testing/tests/runtests.py -v --run-destructive --sysinfo'
            '{no_color} --xml=/tmp/xml-unitests-output --coverage-xml=/tmp/coverage.xml '
            '--transport={transport}'.format(
                python_exec=get_minion_python_executable(options),
                no_color=options.no_color and ' --no-color' or '',
                transport=options.transport
            )
        )
    if options.test_command:
        exitcode = run_ssh_command(options, options.test_command)
        if exitcode != 0:
            print('The execution of the {0!r} SLS failed')
            parser.exit(exitcode)

    if options.download_artifact:
        download_artifacts(options)
# <---- Parser Code --------------------------------------------------------------------------------------------------

if __name__ == '__main__':
    main()
