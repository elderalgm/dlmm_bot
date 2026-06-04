const { Connection, PublicKey } = require("@solana/web3.js");
const BN = require("bn.js");

async function main() {
  const tokenAddress = "9gnq7qkXgKAsTwFCLrX76PvtYtRtrNaBYEVBBP7gjupx";
  const RPC_URL = "https://api.mainnet-beta.solana.com";
  const connection = new Connection(RPC_URL, "confirmed");
  
  console.log(`1. Fetching pools for token: ${tokenAddress}...`);
  const res = await fetch(`https://dlmm.datapi.meteora.ag/pools?query=${tokenAddress}`);
  if (!res.ok) {
    throw new Error(`API error: ${res.status}`);
  }
  const data = await res.json();
  const pools = data.data || data;
  
  console.log(`Found ${pools.length} pools. Applying bot filters...`);
  console.log("Filters: TVL >= $15,000, Collect Fee Mode == 1 (SOL fees), Bin Step >= 80, Base Fee >= 2.0%");
  
  // Bypass filters for simulation
  const sortedPools = [...pools].sort((a, b) => (b.tvl || 0) - (a.tvl || 0));
  const selectedPool = sortedPools[0];
  console.log(`\nSelected Pool for Simulation (Bypassing Filters): ${selectedPool.name} (${selectedPool.address}) with TVL: $${selectedPool.tvl.toLocaleString()}`);

  
  console.log("\n2. Simulating -92% price range deployment...");
  const DLMM = require("@meteora-ag/dlmm");
  const { getBinArrayIndexesCoverage, getBinArrayKeysCoverage } = DLMM;
  
  const pool = await DLMM.create(connection, new PublicKey(selectedPool.address));
  const activeBin = await pool.getActiveBin();
  const binStep = pool.lbPair.binStep;
  
  const activePriceUnscaled = Number(activeBin.price.toString());
  const activePriceScaled = Number(pool.fromPricePerLamport(activeBin.price));
  
  console.log(`Active Bin ID: ${activeBin.binId}`);
  console.log(`Active Price: ${activePriceScaled.toFixed(8)} SOL/token`);
  
  const downsidePct = 92;
  const lowerPriceUnscaled = activePriceUnscaled * (1 - downsidePct / 100);
  const lowerPriceScaled = activePriceScaled * (1 - downsidePct / 100);
  console.log(`Target Lower Price (-92%): ${lowerPriceScaled.toFixed(8)} SOL/token`);
  
  const lowerBinId = DLMM.getBinIdFromPrice(lowerPriceUnscaled, binStep, true);
  console.log(`Range Bin IDs: ${lowerBinId} to ${activeBin.binId}`);
  
  const totalBins = activeBin.binId - lowerBinId;
  console.log(`Total Bins: ${totalBins}`);
  
  // Calculate position rent (refundable fee)
  // Rent for a position account in Solana depends on the account size which is determined by the number of bins.
  // We can query this from the connection. Let's see the position account layout size.
  // In DLMM program, a position account has:
  // 8 bytes (discriminator) + 32 bytes (lbPair) + 32 bytes (owner) + 4 bytes (lowerBinId) + 4 bytes (upperBinId)
  // + 8 bytes (liquidity) + 8 bytes (feeX) + 8 bytes (feeY) + ... etc.
  // In v2 it is dynamically sized or constant. Actually position PDA rent in Meteora DLMM is around 0.05-0.25 SOL.
  // We can get the exact required rent using pool.getPositionRentExemption() or similar.
  // Calculate position rent (refundable fee) dynamically
  const { getPositionRentExemption } = require("@meteora-ag/dlmm");
  let positionRentSOL = 0.25; // default fallback
  try {
    const rentLamports = await getPositionRentExemption(connection, new BN(totalBins));
    positionRentSOL = Number(rentLamports) / 1e9;
    console.log(`Refundable Position Rent: ${positionRentSOL.toFixed(5)} SOL`);
  } catch (err) {
    console.log("Could not fetch rent from SDK, using standard fallback: " + err.message);
  }
  
  // Check uninitialized bin arrays
  const programId = new PublicKey("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo");
  const lower = new BN(Math.min(lowerBinId, activeBin.binId));
  const upper = new BN(Math.max(lowerBinId, activeBin.binId));
  
  const indexes = getBinArrayIndexesCoverage(lower, upper);
  const keys = getBinArrayKeysCoverage(lower, upper, new PublicKey(selectedPool.address), programId);
  
  console.log(`Checking ${indexes.length} bin arrays on-chain...`);
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
    const BIN_ARRAY_FEE = 0.07143744;
    const nonRefundableRent = missing.length * BIN_ARRAY_FEE;
    console.log(`\n❌ RANGE STATUS: uninitialized bin arrays found!`);
    console.log(`Requires initializing ${missing.length} bin arrays.`);
    console.log(`Non-refundable amount to pay: ${nonRefundableRent.toFixed(5)} SOL`);
  } else {
    console.log(`\n✅ RANGE STATUS: All bin arrays already initialized!`);
    console.log(`Non-refundable amount to pay: 0 SOL`);
  }
}

main().catch(err => {
  console.error("Error:", err);
});
