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
verify($reloads === 0 && count($queries) === 0); // Import must be inert.
$result = array('status'=>true, 'num_errors'=>0);
$pending = 'false';
verify(myteldesk_issabel_apply() === true);
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
echo "PASS ".$checks." native PHP Apply boundary checks; no PBX/DB actions\n";
