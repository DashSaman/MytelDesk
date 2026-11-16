<?php
// SPDX-License-Identifier: GPL-3.0-or-later
// Complete native Issabel Apply under its existing PBX service identity.
// No arguments, root escalation, credentials, manual flag UPDATE or extra reload.

function myteldesk_issabel_apply() {
    try {
        global $amp_conf;
        $webroot = is_array($amp_conf) && isset($amp_conf['AMPWEBROOT']) ? $amp_conf['AMPWEBROOT'] : null;
        if (!is_string($webroot) || $webroot === '' || $webroot[0] !== '/' || strpos($webroot, "\0") !== false) {
            return false;
        }
        // Match module_admin before native PRE/POST hooks run: their relative
        // paths are defined from the configured web root's admin directory.
        $admin_directory = rtrim($webroot, '/').'/admin/';
        if (!is_dir($admin_directory) || !@chdir($admin_directory)) {
            return false;
        }
        $result = do_reload();
        if (!is_array($result) || !isset($result['status']) || $result['status'] !== true ||
            !isset($result['num_errors']) || !is_int($result['num_errors']) || $result['num_errors'] !== 0) {
            return false;
        }
        global $db;
        if (!is_object($db) || !is_callable(array($db, 'getOne')) || !class_exists('DB')) {
            return false;
        }
        $pending = $db->getOne("SELECT value FROM admin WHERE variable = 'need_reload'");
        return !DB::IsError($pending) && $pending === 'false';
    } catch (Throwable $error) {
        return false;
    }
}

// Import is inert so the native result boundary can be tested without including
// any PBX configuration or opening a database. The deployed CLI has one fixed action.
if (isset($_SERVER['SCRIPT_FILENAME']) && realpath($_SERVER['SCRIPT_FILENAME']) === __FILE__) {
    $ok = false;
    $level = ob_get_level();
    ob_start();
    ini_set('display_errors', '0');
    try {
        if (PHP_SAPI !== 'cli' || !isset($argc) || $argc !== 1) {
            throw new RuntimeException('Fixed CLI action only');
        }
        // Same supported bootstrap/autoloader as Issabel's module_admin, whose
        // reload command prints errors but otherwise exits0 even when Apply fails.
        $bootstrap_settings['issabelpbx_auth'] = false;
        if (is_readable('/etc/issabelpbx.conf')) {
            require_once '/etc/issabelpbx.conf';
        } elseif (is_readable('/etc/asterisk/issabelpbx.conf')) {
            require_once '/etc/asterisk/issabelpbx.conf';
        } else {
            throw new RuntimeException('Native PBX bootstrap unavailable');
        }
        if (function_exists('fpbx_framework_autoloader')) {
            fpbx_framework_autoloader(true);
        } else {
            ipbx_framework_autoloader(true);
        }
        $ok = myteldesk_issabel_apply();
    } catch (Throwable $error) {
        $ok = false;
    }
    while (ob_get_level() > $level) ob_end_clean();
    // Native bootstrap/retrieve_conf output can contain configuration details;
    // expose only strict completion evidence to the parent process.
    echo json_encode(array('status'=>$ok, 'num_errors'=>$ok ? 0 : 1,
                           'reload_needed'=>$ok ? false : null))."\n";
    exit($ok ? 0 : 1);
}
