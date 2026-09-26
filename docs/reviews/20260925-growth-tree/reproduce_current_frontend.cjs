const fs=require('node:fs'),vm=require('node:vm');
const requests=[];
const context={window:{},document:{addEventListener(){}},DATABASE_MODE:true,currentTermId:1,render(){},apiRequest(url){return new Promise(resolve=>requests.push({url,resolve}));}};
vm.createContext(context);
const source=fs.readFileSync('workbench-assets/workbench-growth.js','utf8').replace('window.renderGrowth = renderGrowth;', 'window.inspectGrowth = () => ({loaded:growthLoaded,loading:growthLoading,data:growthData}); window.renderGrowth = renderGrowth;');
vm.runInContext(source,context);
(async()=>{
 const first=context.window.loadGrowthForest();
 context.currentTermId=2;
 context.window.growthInvalidate();
 const second=context.window.loadGrowthForest();
 requests[0].resolve({term_id:1,students:[]});
 requests[1].resolve({term_id:2,students:[]});
 await Promise.all([first,second]);
 console.log(JSON.stringify({currentTermId:context.currentTermId,requests:requests.map(x=>x.url),state:context.window.inspectGrowth()},null,2));
})();
