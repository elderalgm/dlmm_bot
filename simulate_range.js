const { Connection, PublicKey } = require("@solana/web3.js");
const BN = require("bn.js");

async function main() {
  const RPC_URL = "https://api.mainnet-beta.solana.com"; // Public RPC
  const connection = new Connection(RPC_URL, "confirmed");
  
  const poolAddressStr = "GeQvmpA4ZyToHPX2x3SrPTBHTNex3xP1ehRwHr3fBkPL"; // Pool #4
  const poolPubKey = new PublicKey(poolAddressStr);
  
  console.log("Loading Meteora DLMM SDK...");
  const DLMM = require("@meteora-ag/dlmm");
  const { getBinArrayIndexesCoverage, getBinArrayKeysCoverage } = DLMM;
  
  console.log(`Fetching pool state for ${poolAddressStr}...`);
  const pool = await DLMM.create(connection, poolPubKey);
  const activeBin = await pool.getActiveBin();
  const binStep = pool.lbPair.binStep;
  
  // Calculate price of active bin
  const activePriceUnscaled = Number(activeBin.price.toString());
  const activePriceScaled = Number(pool.fromPricePerLamport(activeBin.price));
  console.log(`Active Bin ID: ${activeBin.binId}`);
  console.log(`Active Price (unscaled): ${activePriceUnscaled}`);
  console.log(`Active Price (scaled): ${activePriceScaled} SOL/three`);
  
  // Calculate lower price target for -92% downside
  const downsidePct = 92;
  const lowerPriceUnscaled = activePriceUnscaled * (1 - downsidePct / 100);
  const lowerPriceScaled = activePriceScaled * (1 - downsidePct / 100);
  console.log(`Lower target price (-92% scaled): ${lowerPriceScaled} SOL/three`);
  
  // Convert price to bin ID using unscaled price
  const lowerBinId = DLMM.getBinIdFromPrice(lowerPriceUnscaled, binStep, true);
  console.log(`Target Range Bin IDs: ${lowerBinId} to ${activeBin.binId}`);
  console.log(`Total Bins to cover: ${activeBin.binId - lowerBinId}`);
  
  // Check bin arrays coverage
  const programId = new PublicKey("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo");
  const lower = new BN(Math.min(lowerBinId, activeBin.binId));
  const upper = new BN(Math.max(lowerBinId, activeBin.binId));
  
  const indexes = getBinArrayIndexesCoverage(lower, upper);
  const keys = getBinArrayKeysCoverage(lower, upper, poolPubKey, programId);
  
  console.log(`Target range covers ${indexes.length} bin arrays.`);
  console.log("Checking on-chain accounts...");
  
  const accounts = await connection.getMultipleAccountsInfo(keys, "confirmed");
  
  const missing = [];
  accounts.forEach((account, idx) => {
    if (!account) {
      missing.push({
        index: indexes[idx].toString(),
        address: keys[idx].toString()
      });
    }
  });
  
  if (missing.length > 0) {
    const BIN_ARRAY_FEE = 0.07143744; // rent fee in SOL per bin array
    const totalRentFee = missing.length * BIN_ARRAY_FEE;
    console.log(`\n❌ SIMULATION RESULT: FAILED`);
    console.log(`Selected range requires ${missing.length} uninitialized bin arrays!`);
    console.log(`Estimated non-refundable rent fee: ~${totalRentFee.toFixed(5)} SOL`);
    console.log("Missing bin array details:");
    missing.forEach(m => {
      console.log(`  Index: ${m.index}, Account PDA: ${m.address}`);
    });
    console.log("\n-> Since there are uninitialized bin arrays, the bot will SKIP this pool to avoid non-refundable fee!");
  } else {
    console.log("\n✅ SIMULATION RESULT: SUCCESS");
    console.log("All bin arrays in the target range are already initialized on-chain.");
    console.log("No non-refundable rent fee is required. The position can be opened safely!");
  }
}

main().catch(err => {
  console.error("Error running simulation:", err);
});
