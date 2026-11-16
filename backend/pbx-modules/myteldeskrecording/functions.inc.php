<?php
// SPDX-License-Identifier: GPL-3.0-or-later
// Issabel retrieve_conf runs enabled *_hookGet_config functions at priority600.
// Use its public extensions::splice API; never replace native macros or options.

function myteldeskrecording_guard() {
    return '$[${DIALPLAN_EXISTS(opdesk-record,s,1)} & "${OPDESK_REC}"="" & "${REC_STATUS}"!="RECORDING" & "${CDR(recordingfile)}"=""]';
}

/** Pure generator transform. Validate the entire supported shape before mutation. */
function myteldeskrecording_inject($generator) {
    if (!is_object($generator) || !isset($generator->_exts) || !is_array($generator->_exts) ||
        !is_callable(array($generator, 'splice')) || !class_exists('ext_gosubif') || !class_exists('ext_return')) {
        throw new RuntimeException('MytelDesk native recording generator unavailable');
    }
    $context = 'sub-record-check';
    $profile = array('exten'=>2, 'out'=>1, 'rg'=>1, 'q'=>1);
    $condition = myteldeskrecording_guard();
    $command = new ext_gosubif($condition, 'opdesk-record,s,1', false, '${ARG1},${ARG2}');
    $marker = $command->output();
    $planned = array();
    $owned = 0;
    foreach ($profile as $extension=>$expected) {
        $key = ' '.$extension.' ';
        if (!isset($generator->_exts[$context][$key]) || !is_array($generator->_exts[$context][$key])) {
            throw new RuntimeException('MytelDesk native recording profile unavailable');
        }
        $rows = $generator->_exts[$context][$key];
        $returns = 0;
        foreach ($rows as $index=>$row) {
            if (!is_array($row) || !isset($row['cmd']) || !is_object($row['cmd']) || !is_callable(array($row['cmd'], 'output'))) {
                throw new RuntimeException('MytelDesk native recording profile malformed');
            }
            $output = $row['cmd']->output();
            if (strpos($output, 'opdesk-record') !== false) {
                if ($output !== $marker || !isset($rows[$index+1]['cmd']) ||
                    !($rows[$index+1]['cmd'] instanceof ext_return)) {
                    throw new RuntimeException('MytelDesk native recording hook conflicts');
                }
                $owned++;
            }
            if (!($row['cmd'] instanceof ext_return)) continue;
            // A labeled return could be jumped to directly, bypassing an inserted row.
            if ($output !== 'Return()' || !empty($row['tag']) || !is_int($index)) {
                throw new RuntimeException('MytelDesk native recording return profile changed');
            }
            $returns++;
            if (!isset($rows[$index-1]['cmd']) || $rows[$index-1]['cmd']->output() !== $marker) {
                $planned[] = array($extension, $index);
            }
        }
        if ($returns !== $expected) {
            throw new RuntimeException('MytelDesk native recording return count changed');
        }
    }
    // A partial foreign/previous transform is not silently adopted.
    if ($owned !== 0 && ($owned !== 5 || count($planned) !== 0)) {
        throw new RuntimeException('MytelDesk native recording hook incomplete');
    }
    foreach (array_reverse($planned) as $site) {
        $generator->splice($context, $site[0], $site[1], clone $command);
    }
    return count($planned);
}

function myteldeskrecording_hookGet_config($engine) {
    if ($engine !== 'asterisk') return;
    global $ext;
    myteldeskrecording_inject($ext);
}
