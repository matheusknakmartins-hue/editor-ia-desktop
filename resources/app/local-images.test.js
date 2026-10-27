const {test}=require('node:test');
const assert=require('node:assert/strict');
const {validatePairs}=require('./local-images');

test('accepts a complete pairing with each image used once',()=>{
  const pairs=[{indices:[0,3]},{indices:[1,2]}];
  assert.equal(validatePairs(pairs,4),pairs);
});
test('rejects duplicate, missing, malformed and out-of-range image assignments',()=>{
  for(const pairs of [[],[{indices:[0,1]},{indices:[1,2]}],
    [{indices:[0,4]},{indices:[1,2]}],[{indices:[0,3]},{indices:[1.5,2]}],
    [{indices:[0]},{indices:[1,2]}],null]){
    assert.throws(()=>validatePairs(pairs,4));
  }
});
