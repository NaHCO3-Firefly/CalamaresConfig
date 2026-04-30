#!/usr/bin/env python3
# SPDX-FileCopyrightText: no
# SPDX-License-Identifier: CC0-1.0

import subprocess
from string import Template

import libcalamares
from libcalamares.utils import check_target_env_call, target_env_call, target_env_process_output
from libcalamares.utils import gettext_path, gettext_languages

import gettext
_translation = gettext.translation("calamares-python",
                                   localedir=gettext_path(),
                                   languages=gettext_languages(),
                                   fallback=True)
_ = _translation.gettext
_n = _translation.ngettext

total_packages = 0
completed_packages = 0
group_packages = 0


def pretty_name():
    return _("Install AUR packages.")


def pretty_status_message():
    if not group_packages:
        if total_packages > 0:
            s = _("Processing AUR packages (%(count)d / %(total)d)")
        else:
            s = _("Install AUR packages.")
    else:
        s = _n("Installing one AUR package.",
               "Installing %(num)d AUR packages.", group_packages)
    return s % {"num": group_packages, "count": completed_packages, "total": total_packages}


def subst_locale(plist):
    locale = libcalamares.globalstorage.value("locale")
    if not locale:
        locale = "en"
    ret = []
    for packagedata in plist:
        if isinstance(packagedata, str):
            packagename = packagedata
        else:
            packagename = packagedata["package"]
        if locale != "en":
            packagename = Template(packagename).safe_substitute(LOCALE=locale)
        elif 'LOCALE' in packagename:
            packagename = None
        if packagename is not None:
            if isinstance(packagedata, str):
                packagedata = packagename
            else:
                packagedata["package"] = packagename
            ret.append(packagedata)
    return ret


def is_installed(pkgname):
    """Check if a package is already installed in the target system."""
    try:
        check_target_env_call(["pacman", "-Q", pkgname])
        return True
    except subprocess.CalledProcessError:
        return False


def filter_uninstalled(package_list):
    """Remove already-installed packages from the list."""
    result = []
    for pkg in package_list:
        name = pkg if isinstance(pkg, str) else pkg.get("package", "")
        if name and is_installed(name):
            libcalamares.utils.debug("Skipping already installed: {}".format(name))
        else:
            result.append(pkg)
    return result


def del_db_lock(lock="/var/lib/pacman/db.lck"):
    check_target_env_call(["rm", "-f", lock])


def run_paru(command, num_retries=0):
    count = 0
    while count <= num_retries:
        count += 1
        try:
            target_env_process_output(command)
            return
        except subprocess.CalledProcessError:
            if count <= num_retries:
                pass
            else:
                raise


def install_packages(pkgs, config):
    del_db_lock()
    command = ["paru", "-S", "--noconfirm", "--noprogressbar"]
    if config.get("needed_only", False):
        command.append("--needed")
    if config.get("disable_download_timeout", False):
        command.append("--disable-download-timeout")
    run_paru(command + pkgs, config.get("num_retries", 0))


def remove_packages(pkgs, config):
    del_db_lock()
    run_paru(["paru", "-Rs", "--noconfirm"] + pkgs, config.get("num_retries", 0))


def run_operations(ops, config):
    global group_packages, completed_packages

    for entry in ops:
        for key in entry.keys():
            package_list = subst_locale(entry[key])

            # Skip packages already installed by earlier steps (e.g. packages@pacman)
            if key in ("install", "try_install", "localInstall"):
                package_list = filter_uninstalled(package_list)

            if not package_list:
                continue

            group_packages = len(package_list)
            libcalamares.job.setprogress(completed_packages * 1.0 / total_packages)

            if key == "install" or key == "try_install":
                try:
                    if all(isinstance(x, str) for x in package_list):
                        install_packages(package_list, config)
                    else:
                        for pkg in package_list:
                            if isinstance(pkg, str):
                                install_packages([pkg], config)
                            else:
                                if pkg.get("pre-script"):
                                    check_target_env_call(pkg["pre-script"].split(" "))
                                install_packages([pkg["package"]], config)
                                if pkg.get("post-script"):
                                    check_target_env_call(pkg["post-script"].split(" "))
                except subprocess.CalledProcessError:
                    if key == "install":
                        raise

            elif key == "remove" or key == "try_remove":
                try:
                    if all(isinstance(x, str) for x in package_list):
                        remove_packages(package_list, config)
                    else:
                        for pkg in package_list:
                            if isinstance(pkg, str):
                                remove_packages([pkg], config)
                            else:
                                if pkg.get("pre-script"):
                                    check_target_env_call(pkg["pre-script"].split(" "))
                                remove_packages([pkg["package"]], config)
                                if pkg.get("post-script"):
                                    check_target_env_call(pkg["post-script"].split(" "))
                except subprocess.CalledProcessError:
                    if key == "remove":
                        raise

            elif key == "localInstall":
                install_packages(package_list, config)

            elif key == "source":
                libcalamares.utils.debug("Package-list from {!s}".format(entry[key]))

            else:
                libcalamares.utils.warning("Unknown paru operation {!s}".format(key))

            completed_packages += len(package_list)

        group_packages = 0


def run():
    global total_packages, completed_packages

    paru_cfg = libcalamares.job.configuration.get("paru", None)
    if paru_cfg is None:
        paru_cfg = dict()
    if type(paru_cfg) is not dict:
        libcalamares.utils.warning("Job configuration *paru* will be ignored.")
        paru_cfg = dict()

    operations = libcalamares.job.configuration.get("operations", [])
    if libcalamares.globalstorage.contains("packageOperations"):
        operations += libcalamares.globalstorage.value("packageOperations")

    total_packages = 0
    completed_packages = 0
    for op in operations:
        for packagelist in op.values():
            total_packages += len(subst_locale(packagelist))

    if not total_packages:
        libcalamares.utils.debug("No AUR packages to install.")
        return None

    skip_if_no_internet = libcalamares.job.configuration.get("skip_if_no_internet", False)
    if skip_if_no_internet and not libcalamares.globalstorage.value("hasInternet"):
        libcalamares.utils.warning("AUR package installation skipped: no internet")
        return None

    update_db = libcalamares.job.configuration.get("update_db", False)
    if update_db and libcalamares.globalstorage.value("hasInternet"):
        del_db_lock()
        run_paru(["paru", "-Sy"], paru_cfg.get("num_retries", 0))

    try:
        run_operations(operations, paru_cfg)
    except subprocess.CalledProcessError as e:
        libcalamares.utils.warning(str(e))
        return (_("AUR Package Manager error"),
                _("paru could not process packages. The command <pre>{!s}</pre> returned error code {!s}.")
                .format(e.cmd, e.returncode))

    libcalamares.job.setprogress(1.0)
    return None
