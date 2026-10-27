const {test}=require('node:test');
const assert=require('node:assert/strict');
const {validatePairs}=require('./local-images');

test('accepts a complete pairing with each image used once',()=>{
  const pairs=[{indices:[0,3]},{indices:[1,2]}];
  assert.equal(validatePairs(pairs,4),pairs);
});
test('enforces scene 1 to scene 2/3 direction for separate input groups',()=>{
  const pairs=[{indices:[0,3]},{indices:[1,2]}];
  assert.equal(validatePairs(pairs,4,2),pairs);
  assert.throws(()=>validatePairs([{indices:[0,1]},{indices:[2,3]}],4,2));
  assert.throws(()=>validatePairs([{indices:[3,0]},{indices:[1,2]}],4,2));
});
test('rejects duplicate, missing, malformed and out-of-range image assignments',()=>{
  for(const pairs of [[],[{indices:[0,1]},{indices:[1,2]}],
    [{indices:[0,4]},{indices:[1,2]}],[{indices:[0,3]},{indices:[1.5,2]}],
    [{indices:[0]},{indices:[1,2]}],null]){
    assert.throws(()=>validatePairs(pairs,4));
  }
});
