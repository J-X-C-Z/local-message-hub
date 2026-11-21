export function dayKey(timestamp){
  if(!Number.isFinite(timestamp))return '';
  const d=new Date(timestamp);return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
}

const summaries=new WeakMap();
const liveReferences=new WeakMap();
function indexReference(group,message,add){
  const key=message.referenceKey;
  if(!key)return;
  const references=liveReferences.get(group),single=references.get(key);
  if(add){
    if(!single)references.set(key,message);
    else if(single instanceof Set)single.add(message);
    else references.set(key,new Set([single,message]));
  }else if(single instanceof Set){
    single.delete(message);if(single.size===1)references.set(key,single.values().next().value);
  }else if(single===message)references.delete(key);
}
function countMessage(summary,message,delta){
  if(message.system)return;
  summary.count+=delta;
  const day=dayKey(message.time);
  if(day){const total=(summary.days.get(day)||0)+delta;if(total)summary.days.set(day,total);else summary.days.delete(day);}
}
export function conversationSummary(group){
  let summary=summaries.get(group);
  if(!summary){
    summary={count:0,days:new Map(),last:null};
    for(const message of group.messages){countMessage(summary,message,1);if(!message.system)summary.last=message;}
    summaries.set(group,summary);
  }
  return summary;
}
function compareMessage(a,b){
  return a.time-b.time||(a.sequence!==undefined&&b.sequence!==undefined?a.sequence-b.sequence:String(a.id).localeCompare(String(b.id)));
}

// Preserve unaffected conversations and normalize/sort only changed messages.
export class LiveConversationIndex {
  constructor({normalize,key,create}){Object.assign(this,{normalize,key,create});this.events=new Map();this.groups=new Map();this.revision=0;}
  clear(){this.events.clear();this.groups.clear();}
  removeEntry(entry,keepGroup=false){
    const group=this.groups.get(entry.key);if(!group)return;
    const index=group.messages.indexOf(entry.message);
    if(index!==-1){
      const summary=conversationSummary(group);group.messages.splice(index,1);countMessage(summary,entry.message,-1);indexReference(group,entry.message,false);
      if(summary.last===entry.message){let i=index-1;while(i>=0&&group.messages[i].system)i--;summary.last=group.messages[i]||null;}
    }
    group.revision=++this.revision;if(!keepGroup&&!group.messages.length)this.groups.delete(entry.key);
  }
  expire(cutoff){
    let changed=false;
    for(const [id,entry] of this.events)if(entry.event.received_at<cutoff){this.removeEntry(entry);this.events.delete(id);changed=true;}
    return changed;
  }
  ingest(events,{replace=true}={}){
    const inserted=[];
    for(const event of events){
      const previous=this.events.get(event.id);if(previous&&(previous.event===event||!replace))continue;
      const key=this.key(event.message);
      const message=this.normalize(event);
      if(previous?.key===key&&compareMessage(previous.message,message)===0&&previous.message.system===message.system){
        const group=this.groups.get(key),items=group.messages;
        let low=0,high=items.length;
        while(low<high){const m=(low+high)>>>1;if(compareMessage(items[m],message)<0)low=m+1;else high=m;}
        while(low<items.length&&items[low]!==previous.message&&compareMessage(items[low],message)===0)low++;
        if(items[low]===previous.message){
          const summary=conversationSummary(group);items[low]=message;if(summary.last===previous.message)summary.last=message;
          indexReference(group,previous.message,false);indexReference(group,message,true);
          group.revision=++this.revision;this.events.set(event.id,{event,key,message});continue;
        }
      }
      if(previous)this.removeEntry(previous,previous.key===key);else inserted.push(event);
      let group=this.groups.get(key);
      if(!group){group={...this.create(event,message,key),messages:[],revision:0};this.groups.set(key,group);liveReferences.set(group,new Map());}
      const list=group.messages,summary=conversationSummary(group);let low=0,high=list.length;
      while (low < high) {
        const middle = (low + high) >>> 1;
        if (compareMessage(list[middle],message) <= 0) low = middle + 1;
        else high = middle;
      }
      list.splice(low,0,message);countMessage(summary,message,1);indexReference(group,message,true);
      if(!message.system&&(!summary.last||compareMessage(summary.last,message)<=0))summary.last=message;
      group.revision=++this.revision;this.events.set(event.id,{event,key,message});
    }
    return inserted;
  }
  sync(events){
    const retained=new Set(events.map(event=>event.id));
    for(const [id,entry] of this.events)if(!retained.has(id)){this.removeEntry(entry);this.events.delete(id);}
    this.ingest(events);
    return [...this.groups.values()];
  }
}

export function nextRender(){
  return new Promise(resolve=>document.hidden?setTimeout(resolve,0):requestAnimationFrame(()=>setTimeout(resolve,0)));
}

export async function conversationExport(conversation,source,yieldTask=()=>globalThis.scheduler?.yield?.()??new Promise(resolve=>setTimeout(resolve,0))){
  const {messages,...meta}=conversation;
  // Capture references and metadata before yielding, so live revisions cannot mix into the export.
  const captured=messages.slice();
  const header=JSON.stringify({exported_at:new Date().toISOString(),source,conversation:meta});
  const parts=[header.slice(0,-2)+(Object.keys(meta).length?',':'')+'"messages":['];
  for(let first=0;first<captured.length;first+=200){
    await yieldTask();
    parts.push(new Blob([(first?',':'')+captured.slice(first,first+200).map(item=>JSON.stringify(item)).join(',')]));
  }
  parts.push(']}}');return new Blob(parts,{type:'application/json'});
}

export function messagePage(messages,start=null,size=100){
  const latest=Math.max(0,messages.length-size);
  const first=start===null?latest:Math.max(0,Math.min(start,latest));
  return {first,end:Math.min(messages.length,first+size),latest,messages:messages.slice(first,first+size)};
}

const referenceCache=new WeakMap();
export function quoteOriginal(conversation,target,replyId){
  if(!target)return null;
  const live=liveReferences.get(conversation);
  if(live){const result=live.get(target);return result&&!(result instanceof Set)&&result.id!==replyId?result:null;}
  const revision=conversation.revision??conversation.messages.length;
  let cached=referenceCache.get(conversation);
  if(cached?.revision!==revision){
    const references=new Map();
    for(const message of conversation.messages){
      const key=message.referenceKey;
      // A duplicate ID is ambiguous even within one conversation.
      if(key)references.set(key,references.has(key)?null:message);
    }
    cached={revision,references};referenceCache.set(conversation,cached);
  }
  const result=cached.references.get(target);
  return result&&result.id!==replyId?result:null;
}

const searchCache=new WeakMap();
export function conversationSearchText(conversation,preview){
  const revision=conversation.revision??conversation.messages.length,previous=searchCache.get(conversation);
  if(previous?.revision===revision&&previous.name===conversation.name)return previous.text;
  const text=(conversation.name+'\n'+conversation.messages.map(preview).join('\n')).toLowerCase();
  searchCache.set(conversation,{revision,name:conversation.name,text});return text;
}
