#!/bin/bash
# SPDX-License-Identifier: GPL-2.0-only
#
# Copyright (C) 2026 Oisin McGrath
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License only.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

set -euo pipefail
export PATH=/usr/bin:/bin
unset CDPATH

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

if (( EUID == 0 )); then
    die "Run this installer as the user who will use NitroTune, not as root."
fi

user_id=$(/usr/bin/id -u)
user_name=$(/usr/bin/id -un) || die "Cannot determine the invoking user."
[[ $user_name =~ ^[a-z_][a-z0-9_-]*$ ]] ||
    die "The invoking username is not safe for a sudoers rule."
account=$(/usr/bin/getent passwd "$user_name") ||
    die "Cannot verify the invoking user in the account database."
IFS=: read -r account_name _ account_id _ <<< "$account"
[[ $account_name == "$user_name" && $account_id == "$user_id" ]] ||
    die "The invoking user's name and UID do not match the account database."

script_dir=$(cd -- "$(/usr/bin/dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
source_file=$script_dir/nitrotune-ec-read.c
[[ -f $source_file && -r $source_file ]] ||
    die "Cannot read $source_file."

trap '' HUP INT TERM
temp_dir=$(/usr/bin/mktemp -d /tmp/nitrotune-ec-read.XXXXXXXXXX) ||
    die "Cannot create a temporary build directory."
cleanup_build() {
    local status=$?
    trap - EXIT
    if [[ -e $temp_dir || -L $temp_dir ]] &&
       { ! /usr/bin/rm -f -- "$temp_dir/nitrotune-ec-read" ||
         ! /usr/bin/rmdir -- "$temp_dir"; }; then
        printf 'ERROR: Could not remove temporary build directory %s\n' "$temp_dir" >&2
        status=1
    fi
    exit "$status"
}
trap cleanup_build EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'Compiling the read-only EC helper as %s...\n' "$user_name"
/usr/bin/cc -std=c11 -O2 -Wall -Wextra -Werror -Wpedantic \
    "$source_file" -o "$temp_dir/nitrotune-ec-read"

printf 'Requesting administrator authorization to install the helper and sudoers rule...\n'
/usr/bin/sudo /usr/bin/env -i PATH=/usr/bin:/bin \
    /bin/bash --noprofile --norc -s -- "$user_name" "$temp_dir/nitrotune-ec-read" <<'ROOT_SCRIPT'
set -euo pipefail
umask 077

user_name=$1
binary=$2
helper=/usr/local/libexec/nitrotune-ec-read
rule=/etc/sudoers.d/nitrotune-ec-read
helper_stage=
rule_stage=
helper_identity=
rule_identity=
created_libexec=0
libexec_identity=
committed=0

die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    local status=$?
    trap - EXIT
    if (( ! committed )); then
        if [[ -n $rule_identity && ! -L $rule && -f $rule ]] &&
           [[ $(/usr/bin/stat -c '%d:%i' -- "$rule") == "$rule_identity" ]]; then
            /usr/bin/rm -f -- "$rule" || {
                printf 'ERROR: Could not roll back %s\n' "$rule" >&2
                status=1
            }
        fi
        if [[ -n $helper_identity && ! -L $helper && -f $helper ]] &&
           [[ $(/usr/bin/stat -c '%d:%i' -- "$helper") == "$helper_identity" ]]; then
            /usr/bin/rm -f -- "$helper" || {
                printf 'ERROR: Could not roll back %s\n' "$helper" >&2
                status=1
            }
        fi
    fi
    if [[ -n $rule_stage ]]; then
        /usr/bin/rm -f -- "$rule_stage" || {
            printf 'ERROR: Could not remove staged rule %s\n' "$rule_stage" >&2
            status=1
        }
    fi
    if [[ -n $helper_stage ]]; then
        /usr/bin/rm -f -- "$helper_stage" || {
            printf 'ERROR: Could not remove staged helper %s\n' "$helper_stage" >&2
            status=1
        }
    fi
    if (( ! committed && created_libexec )) &&
       [[ -n $libexec_identity && ! -L /usr/local/libexec ]] &&
       [[ $(/usr/bin/stat -c '%d:%i' -- /usr/local/libexec) == "$libexec_identity" ]]; then
        /usr/bin/rmdir -- /usr/local/libexec || {
            printf 'ERROR: Could not remove created directory /usr/local/libexec\n' >&2
            status=1
        }
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

check_directory() {
    local directory=$1 owner mode
    [[ ! -L $directory && -d $directory ]] ||
        die "$directory must be an existing directory, not a symlink."
    read -r owner mode < <(/usr/bin/stat -c '%u %a' -- "$directory")
    [[ $owner == 0 && $mode =~ ^[0-7]+$ ]] ||
        die "$directory must be owned by root."
    (( (8#$mode & 0022) == 0 )) ||
        die "$directory must not be writable by group or other users."
}

for directory in / /usr /usr/local /etc /etc/sudoers.d; do
    check_directory "$directory"
done

if [[ ! -e /usr/local/libexec && ! -L /usr/local/libexec ]]; then
    printf 'Creating /usr/local/libexec...\n'
    trap '' HUP INT TERM
    if /usr/bin/mkdir -m 0755 -- /usr/local/libexec; then
        created_libexec=1
        libexec_identity=$(/usr/bin/stat -c '%d:%i' -- /usr/local/libexec)
        /usr/bin/chmod 0755 -- /usr/local/libexec
        [[ $(/usr/bin/stat -c '%a' -- /usr/local/libexec) == 755 ]] ||
            die "Created /usr/local/libexec does not have mode 0755."
    else
        die "Cannot safely create /usr/local/libexec."
    fi
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM
fi
check_directory /usr/local/libexec
/usr/bin/runuser -u "$user_name" -- /usr/bin/test -x /usr/local/libexec ||
    die "$user_name cannot traverse /usr/local/libexec."

for destination in "$helper" "$rule"; do
    [[ ! -e $destination && ! -L $destination ]] ||
        die "Destination already exists: $destination"
done

printf 'Staging the helper and restricted sudoers rule...\n'
trap '' HUP INT TERM
helper_stage=$(/usr/bin/mktemp /usr/local/libexec/.nitrotune-ec-read.XXXXXXXXXX)
rule_stage=$(/usr/bin/mktemp /etc/sudoers.d/.nitrotune-ec-read.XXXXXXXXXX)
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Read the user-owned build as that user, not as root.
/usr/bin/runuser -u "$user_name" -- /usr/bin/cat -- "$binary" > "$helper_stage"
/usr/bin/runuser -u "$user_name" -- /usr/bin/rm -- "$binary"
/usr/bin/runuser -u "$user_name" -- /usr/bin/rmdir -- "${binary%/*}"
/usr/bin/chown root:root -- "$helper_stage"
/usr/bin/chmod 0755 -- "$helper_stage"
printf '%s ALL=(root) TIMEOUT=2s NOPASSWD: NOSETENV: /usr/local/libexec/nitrotune-ec-read ""\n' \
    "$user_name" > "$rule_stage"
/usr/bin/chown root:root -- "$rule_stage"
/usr/bin/chmod 0440 -- "$rule_stage"

printf 'Validating the staged sudoers rule...\n'
/usr/sbin/visudo -cf "$rule_stage" ||
    die "The staged sudoers rule did not pass visudo validation."
helper_identity=$(/usr/bin/stat -c '%d:%i' -- "$helper_stage")
rule_identity=$(/usr/bin/stat -c '%d:%i' -- "$rule_stage")

printf 'Installing the helper and sudoers rule without replacing existing files...\n'
/usr/bin/ln -T -- "$helper_stage" "$helper" ||
    die "Cannot install the helper without overwriting an existing destination."
/usr/bin/runuser -u "$user_name" -- /usr/bin/test -x "$helper" ||
    die "$user_name cannot execute the installed helper."
/usr/bin/ln -T -- "$rule_stage" "$rule" ||
    die "Cannot install the sudoers rule without overwriting an existing destination."

printf 'Validating the complete sudoers configuration...\n'
/usr/sbin/visudo -c ||
    die "The complete sudoers configuration did not pass validation."

printf 'WARNING: Sudoers validation does not prove the rule is included or takes precedence for %s.\n' \
    "$user_name" >&2

/usr/bin/rm -- "$helper_stage" "$rule_stage"
printf 'Installed the helper and restricted sudoers rule for %s.\n' "$user_name"
committed=1
ROOT_SCRIPT
