<?php
// Native PHP boundary fixture only: no PBX includes, DB connection or commands.
// Root executes with PHP7.4 in an isolated fixture; the adapter import is inert.
if ($argc !== 2 || !is_file($argv[1])) {
    fwrite(STDERR, "Missing native Apply adapter\n");
    exit(2);
}
require $argv[1];
$checks = 0;
$reloads = 0;
$result = array();
$pending = 'true';
$queries = array();
$initial_cwd = getcwd();
$fixture_dir = sys_get_temp_dir().'/myteldesk-apply-cwd-'.uniqid('', true);
if (!mkdir($fixture_dir, 0700) || !mkdir($fixture_dir.'/web', 0700) ||
    !mkdir($fixture_dir.'/web/admin', 0700) || !mkdir($fixture_dir.'/denied', 0700) ||
    !mkdir($fixture_dir.'/denied/admin', 0700)) exit(2);
file_put_contents($fixture_dir.'/web/admin/pre-hook.php', '<?php $GLOBALS["hook_trace"][] = array("pre", getcwd());');
file_put_contents($fixture_dir.'/web/admin/post-hook.php', '<?php $GLOBALS["hook_trace"][] = array("post", getcwd());');
file_put_contents($fixture_dir.'/plain-file', 'not a directory');
$amp_conf = array('AMPWEBROOT'=>$fixture_dir.'/web');
$hook_trace = array();
class DB {
    public static function IsError($value) { return $value instanceof Exception; }
}
class FixtureDatabase {
    public function getOne($sql) {
        global $pending, $queries;
        $queries[] = $sql;
        if ($pending === 'throw') throw new Exception('private database error');
        return $pending;
    }
}
$db = new FixtureDatabase();
function do_reload() {
    global $reloads, $result;
    $reloads++;
    // Pure fixture-owned PHP includes model native relative PRE/POST hook
    // lookup from module_admin's configured AMPWEBROOT/admin working directory.
    // Neither include starts a command or reads any PBX configuration.
    @include './pre-hook.php';
    @include './post-hook.php';
    return $result;
}
function verify($condition) {
    global $checks;
    $checks++;
    if (!$condition) {
        fwrite(STDERR, "Native Apply contract failure #".$checks."\n");
        exit(1);
    }
}
verify($reloads === 0 && count($queries) === 0 && getcwd() === $initial_cwd); // Import must be inert.
$result = array('status'=>true, 'num_errors'=>0);
$pending = 'false';
verify(myteldesk_issabel_apply() === true);
verify(getcwd() === realpath($fixture_dir.'/web/admin'));
verify($hook_trace === array(array('pre', getcwd()), array('post', getcwd())));
verify($reloads === 1 && $queries === array("SELECT value FROM admin WHERE variable = 'need_reload'"));
foreach (array(
    array('status'=>false, 'num_errors'=>0), array('status'=>'true', 'num_errors'=>0),
    array('status'=>1, 'num_errors'=>0), array('status'=>true, 'num_errors'=>1),
    array('status'=>true, 'num_errors'=>'0'), array('status'=>true, 'num_errors'=>null),
    array('status'=>true, 'num_errors'=>false), array('status'=>true), array(), null
) as $case) {
    $result = $case;
    $queries = array();
    verify(myteldesk_issabel_apply() === false);
    verify(count($queries) === 0); // Failed native Apply cannot claim completion.
}
$result = array('status'=>true, 'num_errors'=>0);
foreach (array('true', false, null, '', '0', new Exception('read failed'), 'throw') as $value) {
    $pending = $value;
    verify(myteldesk_issabel_apply() === false);
}
$pending = 'false';
$db = null;
verify(myteldesk_issabel_apply() === false);
$db = new FixtureDatabase();
// Invalid or inaccessible native working directories must stop before reload.
chmod($fixture_dir.'/denied/admin', 0000);
foreach (array(array(), array('AMPWEBROOT'=>null), array('AMPWEBROOT'=>array()),
    array('AMPWEBROOT'=>''), array('AMPWEBROOT'=>'relative-web-root'),
    array('AMPWEBROOT'=>"/bad\0root"), array('AMPWEBROOT'=>$fixture_dir.'/plain-file'),
    array('AMPWEBROOT'=>$fixture_dir.'/missing'), array('AMPWEBROOT'=>$fixture_dir.'/denied')) as $bad) {
    $amp_conf = $bad;
    $previous = $reloads;
    verify(myteldesk_issabel_apply() === false);
    verify($reloads === $previous);
}
$amp_conf = array('AMPWEBROOT'=>$fixture_dir.'/web/');
verify(myteldesk_issabel_apply() === true); // Native trailing slash is supported.
chdir($initial_cwd);
chmod($fixture_dir.'/denied/admin', 0700);
unlink($fixture_dir.'/web/admin/pre-hook.php');
unlink($fixture_dir.'/web/admin/post-hook.php');
unlink($fixture_dir.'/plain-file');
rmdir($fixture_dir.'/web/admin');
rmdir($fixture_dir.'/web');
rmdir($fixture_dir.'/denied/admin');
rmdir($fixture_dir.'/denied');
rmdir($fixture_dir);
echo "PASS ".$checks." native PHP Apply boundary checks; no PBX/DB actions\n";
