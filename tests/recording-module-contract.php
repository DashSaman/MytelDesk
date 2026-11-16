<?php
// Inert native API boundary fixture: no PBX includes, SQL connection or commands.
$path = isset($argv[1]) ? $argv[1] : null;
if (!$path || !is_file($path)) { fwrite(STDERR, "Missing recording module adapter\n"); exit(1); }
require $path;
if (!function_exists('myteldesk_recording_module')) { fwrite(STDERR, "Missing recording module API\n"); exit(1); }
class DB { static function IsError($value) { return $value instanceof Exception; } }
class FixtureDatabase {
    function getAll($sql, $mode = null) {
        if ($sql !== "SELECT modulename,version,enabled FROM modules WHERE modulename = 'myteldeskrecording'") {
            throw new Exception('Unexpected SQL');
        }
        return $GLOBALS['fixture_rows'];
    }
}
function module_install($name) { $GLOBALS['fixture_calls'][] = array('install', $name); return $GLOBALS['fixture_result']; }
function module_enable($name) { $GLOBALS['fixture_calls'][] = array('enable', $name); return $GLOBALS['fixture_result']; }
function module_uninstall($name) { $GLOBALS['fixture_calls'][] = array('uninstall', $name); return $GLOBALS['fixture_result']; }
$root = sys_get_temp_dir().'/myteldesk-module-'.getmypid().'-'.bin2hex(random_bytes(4));
mkdir($root, 0700); mkdir($root.'/admin', 0700);
$original = getcwd(); $db = new FixtureDatabase();
$amp_conf = array('AMPWEBROOT'=>$root);
$checks = 0; $failures = 0;
function check($ok, $label) {
    $GLOBALS['checks']++;
    if (!$ok) { $GLOBALS['failures']++; fwrite(STDERR, 'FAIL '.$label."\n"); }
}
function fixture($rows, $result = true) {
    $GLOBALS['fixture_rows'] = $rows; $GLOBALS['fixture_result'] = $result; $GLOBALS['fixture_calls'] = array();
}
$row = array('modulename'=>'myteldeskrecording','version'=>'1.0.0','enabled'=>'1');
try {
    fixture(array()); $value = myteldesk_recording_module('inspect');
    check($value['status'] === true && $value['module'] === null, 'inspect absent');
    check($value['admin'] === $root.'/admin' && getcwd() === $root.'/admin', 'native administration working directory');
    check($fixture_calls === array(), 'inspect performs no mutation');
    fixture(array($row)); $value = myteldesk_recording_module('inspect');
    check($value['module'] === array('version'=>'1.0.0','enabled'=>true), 'strict native row projection');
    foreach (array('install','enable') as $action) {
        fixture(array($row)); $value = myteldesk_recording_module($action);
        check($value['status'] === true && $value['module']['enabled'] === true, $action.' true with matching state');
        check($fixture_calls === array(array($action,'myteldeskrecording')), $action.' only one fixed module');
        foreach (array(false,0,1,null,'true',array()) as $bad) {
            fixture(array($row), $bad); $value = myteldesk_recording_module($action);
            check($value['status'] === false, $action.' rejects non-true native return');
        }
    }
    fixture(array()); $value = myteldesk_recording_module('uninstall');
    check($value['status'] === true && $value['module'] === null, 'uninstall strict absent readback');
    check($fixture_calls === array(array('uninstall','myteldeskrecording')), 'uninstall only fixed module');
    fixture(array($row)); check(myteldesk_recording_module('uninstall')['status'] === false, 'uninstall refuses remaining row');
    foreach (array(array(), array(array('modulename'=>'other','version'=>'1.0.0','enabled'=>'1')), array(array('modulename'=>'myteldeskrecording','version'=>'2.0.0','enabled'=>'1')), array(array('modulename'=>'myteldeskrecording','version'=>'1.0.0','enabled'=>'0'))) as $rows) {
        fixture($rows); check(myteldesk_recording_module('install')['status'] === false, 'install refuses inconsistent poststate');
    }
    foreach (array(new Exception('synthetic metadata failure'), array($row,$row), array(array('modulename'=>'myteldeskrecording','version'=>'1.0.0','enabled'=>'yes'))) as $rows) {
        fixture($rows); check(myteldesk_recording_module('inspect')['status'] === false, 'metadata error/duplicate/invalid state refused');
    }
    fixture(array($row)); check(myteldesk_recording_module('deleteall')['status'] === false && !$fixture_calls, 'unknown action cannot mutate');
    foreach (array('', 'relative', $root.'/missing', $root."\0tail") as $bad) {
        $amp_conf['AMPWEBROOT'] = $bad; fixture(array($row));
        check(myteldesk_recording_module('install')['status'] === false && !$fixture_calls, 'invalid webroot refuses before native action');
    }
} finally { chdir($original); rmdir($root.'/admin'); rmdir($root); }
echo $checks.' checks; '.$failures." failures\n";
exit($failures ? 1 : 0);
