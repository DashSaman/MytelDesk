<?php
// Pure generator contract. Optional third argument loads captured public native
// extensions.class.php; it neither bootstraps Issabel nor executes a PBX command.
if ($argc < 2 || $argc > 3) exit(2);
if ($argc === 3) {
    require $argv[2];
} else {
    class extension { public $data; function __construct($v='') {$this->data=$v;} function output(){return $this->data;} }
    class ext_return extends extension {function output(){return 'Return('.$this->data.')';}}
    class ext_noop extends extension {function output(){return 'Noop('.$this->data.')';}}
    class ext_gosubif extends extension {
        private $condition; private $target; private $args;
        function __construct($condition,$target,$false=false,$args='',$falseargs='') {$this->condition=$condition;$this->target=$target;$this->args=$args;}
        function output(){return 'GosubIf('.$this->condition.'?'.$this->target.'('.$this->args.'))';}
    }
    class extensions {
        public $_exts=array();
        function add($context,$extension,$tag,$cmd) {$key=' '.$extension.' ';$this->_exts[$context][$key][]=array('basetag'=>isset($this->_exts[$context][$key])?'n':'1','tag'=>$tag,'addpri'=>false,'cmd'=>$cmd);}
        function splice($context,$extension,$index,$command) {array_splice($this->_exts[$context][' '.$extension.' '],$index,0,array(array('basetag'=>'n','tag'=>'','addpri'=>'','cmd'=>$command)));}
    }
}
require $argv[1];
$checks=0;
function verify($ok) {global $checks;$checks++;if(!$ok){fwrite(STDERR,'Native recording module assertion '.$checks." failed\n");exit(1);}}
function fixture() {
    $e=new extensions();
    foreach (array('exten'=>2,'out'=>1,'rg'=>1,'q'=>1,'record'=>1,'recq'=>1,'recconf'=>1,'s'=>2) as $name=>$count) {
        for($i=0;$i<$count;$i++) {$e->add('sub-record-check',$name,'native-'.$i,new ext_noop('native-command-'.$i));$e->add('sub-record-check',$name,'',new ext_return(''));}
    }
    $e->add('unrelated','s','operator-label',new ext_noop('preserve native Dial options and SIP header handler'));
    return $e;
}
$e=fixture();$before=serialize($e->_exts);
verify(myteldeskrecording_inject($e)===5);
verify(myteldeskrecording_inject($e)===0); // same generator object is idempotent
$after=$e->_exts;
foreach (array('exten'=>2,'out'=>1,'rg'=>1,'q'=>1) as $name=>$count) {
    $rows=$e->_exts['sub-record-check'][' '.$name.' '];$kept=array();$inserted=0;
    foreach($rows as $i=>$row) {
        if($row['cmd'] instanceof ext_gosubif) {
            $inserted++;
            verify(isset($rows[$i+1]) && $rows[$i+1]['cmd'] instanceof ext_return);
            verify(strpos($row['cmd']->output(),'opdesk-record,s,1(${ARG1},${ARG2})')!==false);
        } else $kept[]=$row;
    }
    verify($inserted===$count);
    $original=unserialize($before);verify(serialize($kept)===serialize($original['sub-record-check'][' '.$name.' ']));
}
foreach(array('record','recq','recconf','s') as $name) { $original=unserialize($before);verify(serialize($after['sub-record-check'][' '.$name.' '])===serialize($original['sub-record-check'][' '.$name.' '])); }
$original=unserialize($before);verify(serialize($after['unrelated'])===serialize($original['unrelated']));
foreach(array('DIALPLAN_EXISTS(opdesk-record,s,1)','"${OPDESK_REC}"=""','"${REC_STATUS}"!="RECORDING"','"${CDR(recordingfile)}"=""') as $part) verify(strpos(myteldeskrecording_guard(),$part)!==false);
// Unknown native shape or foreign partial injection refuses before any mutation.
foreach(array('missing','extra','label','partial') as $bad) {
    $e=fixture();
    if($bad==='missing') unset($e->_exts['sub-record-check'][' q ']);
    if($bad==='extra') $e->add('sub-record-check','out','',new ext_return(''));
    if($bad==='label') $e->_exts['sub-record-check'][' out '][1]['tag']='operator-return';
    if($bad==='partial') $e->splice('sub-record-check','out',1,new ext_gosubif(myteldeskrecording_guard(),'opdesk-record,s,1',false,'${ARG1},${ARG2}'));
    $snapshot=serialize($e->_exts);$refused=false;
    try {myteldeskrecording_inject($e);} catch(RuntimeException $error) {$refused=true;}
    verify($refused);verify(serialize($e->_exts)===$snapshot);
}
global $ext;$ext=fixture();$snapshot=serialize($ext->_exts);myteldeskrecording_hookGet_config('other');verify(serialize($ext->_exts)===$snapshot);
myteldeskrecording_hookGet_config('asterisk');verify(myteldeskrecording_inject($ext)===0);
echo 'PASS '.$checks." native recording generator checks; no PBX/DB/actions\n";
