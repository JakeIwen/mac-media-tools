/* Shared browser/Node timing calculations. No musical-correctness score. */
const Rhythm = (() => {
  function validate(reference) {
    return reference && Number.isFinite(reference.bpm) && reference.bpm>=30 && reference.bpm<=300 &&
      Number.isFinite(reference.offset) && reference.offset>=0 &&
      [1,2,3,4].includes(reference.subdivisions) && [2,3,4,6].includes(reference.beats);
  }
  function project(events, reference) {
    if(!validate(reference)) throw Error('Invalid reference grid');
    const step=60/reference.bpm/reference.subdivisions, slots=reference.beats*reference.subdivisions;
    return events.filter(e=>e.enabled).map(e=>{
      const index=Math.round((e.time-reference.offset)/step), target=reference.offset+index*step;
      return {...e,index,target,error:(e.time-target)*1000,bar:Math.floor(index/slots),slot:((index%slots)+slots)%slots};
    });
  }
  function stats(events) {
    if(!events.length)return {count:0,mean:null,spread:null};
    const mean=events.reduce((sum,e)=>sum+e.error,0)/events.length;
    return {count:events.length,mean,spread:Math.sqrt(events.reduce((sum,e)=>sum+(e.error-mean)**2,0)/events.length)};
  }
  function session(value, report) {
    if(!value || value.version!==1 || value.source!==report.source || Math.abs(value.duration-report.duration)>.001 ||
       !Number.isFinite(value.duration) || !validate(value.reference) || value.reference.offset>report.duration ||
       !Array.isArray(value.events) || value.events.length>100000)throw Error('This session does not match the recording or has invalid settings.');
    const ids=new Set();
    for(const e of value.events){
      if(!Number.isSafeInteger(e.id)||ids.has(e.id)||!Number.isFinite(e.time)||e.time<0||e.time>report.duration||
         !Number.isFinite(e.strength)||e.strength<0||e.strength>1||typeof e.enabled!=='boolean')throw Error('Invalid attack data');
      ids.add(e.id);
    }
    return {reference:{...value.reference},events:value.events.map(e=>({...e}))};
  }
  return {validate,project,stats,session};
})();
if(typeof module!=='undefined')module.exports=Rhythm;
