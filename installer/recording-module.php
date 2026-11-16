<?php
// SPDX-License-Identifier: GPL-3.0-or-later
// One native module API boundary. Never invokes module_admin's global setPerms.
function myteldesk_recording_module($action) {
    $failed = array('status'=>false, 'admin'=>null, 'module'=>null, 'reload_needed'=>null);
    try {
        global $amp_conf, $db;
        if (!in_array($action, array('inspect','install','enable','uninstall'), true)) return $failed;
        $webroot = is_array($amp_conf) && isset($amp_conf['AMPWEBROOT']) ? $amp_conf['AMPWEBROOT'] : null;
        if (!is_string($webroot) || $webroot === '' || $webroot[0] !== '/' || strpos($webroot, "\0") !== false) return $failed;
        $admin = rtrim($webroot, '/').'/admin';
        if (!is_dir($admin) || !@chdir($admin) || !is_object($db) ||
            !is_callable(array($db,'getAll')) || !is_callable(array($db,'getOne')) || !class_exists('DB')) return $failed;
        if ($action !== 'inspect') {
            $function = 'module_'.$action;
            if (!function_exists($function) || $function('myteldeskrecording') !== true) return $failed;
        }
        $rows = $db->getAll("SELECT modulename,version,enabled FROM modules WHERE modulename = 'myteldeskrecording'", DB_FETCHMODE_ASSOC);
        if (DB::IsError($rows) || !is_array($rows) || count($rows) > 1) return $failed;
        $module = null;
        if (count($rows) === 1) {
            $row = reset($rows);
            if (!is_array($row) || !isset($row['modulename'],$row['version'],$row['enabled']) ||
                $row['modulename'] !== 'myteldeskrecording' || !is_string($row['version']) ||
                !preg_match('/^[0-9]+\.[0-9]+\.[0-9]+$/D',$row['version']) ||
                !in_array($row['enabled'],array('0','1',0,1),true)) return $failed;
            $module = array('version'=>$row['version'],'enabled'=>($row['enabled'] === '1' || $row['enabled'] === 1));
        }
        if ($action === 'uninstall' && $module !== null) return $failed;
        if (($action === 'install' || $action === 'enable') &&
            ($module === null || $module['version'] !== '1.0.0' || $module['enabled'] !== true)) return $failed;
        $pending = $db->getOne("SELECT value FROM admin WHERE variable = 'need_reload'");
        if (DB::IsError($pending) || !in_array($pending,array('true','false'),true)) return $failed;
        return array('status'=>true,'admin'=>$admin,'module'=>$module,'reload_needed'=>$pending === 'true');
    } catch (Throwable $error) { return $failed; }
}

// Importing this file is inert for the PHP contract fixture.
if (isset($_SERVER['SCRIPT_FILENAME']) && realpath($_SERVER['SCRIPT_FILENAME']) === __FILE__) {
    $value = array('status'=>false); $level = ob_get_level(); ob_start();
    ini_set('display_errors','0');
    try {
        if (PHP_SAPI !== 'cli' || !isset($argc) || $argc !== 2) throw new RuntimeException('Fixed module action required');
        $bootstrap_settings['issabelpbx_auth'] = false;
        if (is_readable('/etc/issabelpbx.conf')) require_once '/etc/issabelpbx.conf';
        elseif (is_readable('/etc/asterisk/issabelpbx.conf')) require_once '/etc/asterisk/issabelpbx.conf';
        else throw new RuntimeException('Native bootstrap unavailable');
        if (function_exists('fpbx_framework_autoloader')) fpbx_framework_autoloader(true);
        else ipbx_framework_autoloader(true);
        $value = myteldesk_recording_module($argv[1]);
    } catch (Throwable $error) { $value = array('status'=>false); }
    while (ob_get_level() > $level) ob_end_clean();
    echo json_encode($value)."\n";
    exit(isset($value['status']) && $value['status'] === true ? 0 : 1);
}
