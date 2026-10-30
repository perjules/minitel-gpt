// Exercise the real private-console script against DOM primitives and fake HTTP.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const script = JSON.parse(fs.readFileSync(0, 'utf8')).script;
const elements = Object.fromEntries(['output','choose','commands','field','next','previous','submit','username','label','lock']
 .map(id=>[id,{value:'',type:'password',hidden:false,disabled:false,textContent:'',events:{},
 addEventListener(event,fn){this.events[event]=fn;},focus(){}}]));
global.document={getElementById:id=>elements[id]};
const windowEvents={};
global.window={addEventListener:(event,fn)=>{windowEvents[event]=fn;}};
let timeout;
global.setTimeout=fn=>{timeout=fn;return 1;};
global.clearTimeout=()=>{timeout=null;};
vm.runInThisContext(script+'\nglobal.privateSend=send;global.privateLock=lock;');
let calls=[];
let nextResult={closed:false,secret:true,display:'Password prompt',expires_in:300000};
global.fetch=async(url,options)=>{
 calls.push({url,data:JSON.parse(options.body)});
 return {ok:true,json:async()=>nextResult};
};
const tick=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 elements.username.value='Alice';
 await privateSend('open');
 assert.equal(elements.field.type,'password');
 assert.equal(elements.commands.hidden,false);
 nextResult={closed:false,secret:false,display:'FIRST PAGE',more:true,previous:false,expires_in:300000};
 await privateSend('command','T: long text');
 assert.equal(elements.field.disabled,false); // Commands are accepted before the last page.
 elements.field.value='M: partially typed';
 nextResult={closed:false,secret:false,display:'SECOND PAGE',more:false,previous:true,expires_in:300000};
 await privateSend('next');
 assert.equal(elements.field.value,'M: partially typed');
 assert.equal(elements.previous.hidden,false);
 nextResult={closed:false,secret:false,display:'FIRST PAGE',more:true,previous:false,expires_in:300000};
 await privateSend('previous');
 assert.equal(elements.field.value,'M: partially typed');
 elements.field.value='synthetic secret';
 nextResult={closed:false,secret:false,display:'PRIVATE DAY',expires_in:300000};
 elements.commands.events.submit({preventDefault(){}});
 assert.equal(elements.field.value,'');
 await tick();
 assert.equal(elements.field.type,'text');
 assert.equal(elements.output.textContent,'PRIVATE DAY');
 assert.equal(calls.at(-1).data.text,'synthetic secret');
 timeout();
 assert.equal(elements.output.textContent,'Journal verrouillé.');
 assert.equal(elements.field.type,'password');
 assert.equal(elements.commands.hidden,true);
 assert.equal(calls.at(-1).data.action,'lock');
 let release;
 global.fetch=async(url,options)=>JSON.parse(options.body).action==='lock'
  ? {ok:true,json:async()=>({closed:true})}
  : {ok:true,json:()=>new Promise(resolve=>{release=resolve;})};
 const pending=privateSend('open');
 await tick();
 privateLock();
 release({closed:false,secret:false,display:'LATE PRIVATE DAY',expires_in:300000});
 await pending;
 assert.equal(elements.output.textContent,'Journal verrouillé.');
 assert.equal(elements.commands.hidden,true);
 windowEvents.pageshow({persisted:true});
 assert.equal(elements.field.value,'');
})().catch(error=>{console.error(error);process.exitCode=1;});
