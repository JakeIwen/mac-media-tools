const {test}=require('node:test'),assert=require('node:assert/strict');
const R=require('../scripts/rhythm_core.js');
const reference={bpm:120,offset:1,subdivisions:2,beats:4};
const event=(id,time,enabled=true)=>({id,time,enabled,strength:.5});
test('early and late offsets, bar slots and excluded attacks',()=>{
 const p=R.project([event(1,1.02),event(2,1.23),event(3,3.01),event(4,2,false)],reference);
 assert.equal(p.length,3);assert.ok(Math.abs(p[0].error-20)<1e-8);assert.ok(Math.abs(p[1].error+20)<1e-8);
 assert.equal(p[1].slot,1);assert.equal(p[2].bar,1);assert.equal(p[2].slot,0);
});
test('known uneven pattern has zero mean and 20ms spread',()=>{
 const s=R.stats(R.project([event(0,1.02),event(1,1.23)],reference));
 assert.ok(Math.abs(s.mean)<1e-8);assert.ok(Math.abs(s.spread-20)<1e-8);
 assert.equal(R.stats([]).mean,null);
});
test('reference changes are recomputed and pre-anchor bars remain distinct',()=>{
 assert.equal(R.project([event(0,.75)],reference)[0].bar,-1);
 const shifted=R.project([event(0,1.02)],{...reference,offset:1.02});assert.equal(shifted[0].error,0);
 assert.throws(()=>R.project([],{...reference,bpm:0}));
});
test('session validates identity, finite settings, duplicate IDs and times',()=>{
 const report={source:'x.wav',duration:12};const saved={...report,version:1,reference,events:[event(0,1)]};
 assert.equal(R.session(saved,report).events.length,1);
 for(const bad of [{...saved,source:'y.wav'},{...saved,duration:NaN},{...saved,events:[event(0,99)]},{...saved,events:[event(0,1),event(0,2)]},{...saved,reference:{...reference,bpm:Infinity}}])assert.throws(()=>R.session(bad,report));
});
