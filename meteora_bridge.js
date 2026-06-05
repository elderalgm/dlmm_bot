const { Connection, PublicKey, Keypair, VersionedTransaction, Transaction, sendAndConfirmTransaction, ComputeBudgetProgram } = require("@solana/web3.js");
const DLMM = require("@meteora-ag/dlmm");
const { getBinArrayIndexesCoverage, getBinArrayKeysCoverage, getPositionRentExemption, StrategyType } = DLMM;
const BN = require("bn.js");
const bs58Import = require("bs58");
const bs58 = bs58Import.default || bs58Import;
const { ed25519 } = require("@noble/curves/ed25519");

const fs = require("fs");
const path = require("path");

const CONFIG_PATH = path.join(__dirname, "config.json");

// ─── Helpers ──────────────────────────────────────────────────
function readConfig() {
  let config = {};
  if (fs.existsSync(CONFIG_PATH)) {
    config = JSON.parse(fs.readFileSync(CONFIG_PATH, "utf8"));
  }
  return {
    rpc_url: process.env.SOLANA_RPC_URL || config.rpc_url || "YOUR_SOLANA_RPC_URL",
    wallet_private_key: process.env.WALLET_PRIVATE_KEY || config.wallet_private_key || "YOUR_WALLET_PRIVATE_KEY_BASE58",
    telegram_token: process.env.TELEGRAM_TOKEN || config.telegram_token || "YOUR_TELEGRAM_BOT_TOKEN",
    chat_id: process.env.TELEGRAM_CHAT_ID || config.chat_id || "YOUR_TELEGRAM_CHAT_ID",
    gmgn_api_key: process.env.GMGN_API_KEY || config.gmgn_api_key || "gmgn_bd2444c0472e8194980316c0ce4077f5",
    dry_run: process.env.DRY_RUN !== undefined ? process.env.DRY_RUN === "true" : (config.dry_run !== undefined ? config.dry_run : true),
    gas_reserve: parseFloat(process.env.GAS_RESERVE || config.gas_reserve || 0.05),
    min_tvl: parseFloat(process.env.MIN_TVL || config.min_tvl || 15000),
    min_bin_step: parseInt(process.env.MIN_BIN_STEP || config.min_bin_step || 80),
    min_base_fee_pct: parseFloat(process.env.MIN_BASE_FEE_PCT || config.min_base_fee_pct || 2.0)
  };
}

function getSolanaConnection(config) {
  return new Connection(config.rpc_url, "confirmed");
}

function getWalletKeypair(config) {
  if (!config.wallet_private_key || config.wallet_private_key.startsWith("YOUR_")) {
    throw new Error("Wallet private key not configured in config.json");
  }
  return Keypair.fromSecretKey(bs58.decode(config.wallet_private_key));
}

async function sendAndConfirmTransactionWithFees(connection, tx, signers) {
  if (tx.instructions) {
    let hasComputeBudget = false;
    for (const inst of tx.instructions) {
      if (inst.programId.equals(ComputeBudgetProgram.programId)) {
        hasComputeBudget = true;
        break;
      }
    }
    
    if (!hasComputeBudget) {
      const limitInst = ComputeBudgetProgram.setComputeUnitLimit({
        units: 800000
      });
      const priceInst = ComputeBudgetProgram.setComputeUnitPrice({
        microLamports: 1500000
      });
      tx.instructions.unshift(limitInst, priceInst);
    }
  }
  return await sendAndConfirmTransaction(connection, tx, signers, {
    commitment: "confirmed",
    preflightCommitment: "confirmed"
  });
}

// ─── Command: get-balance ─────────────────────────────────────
async function cmdGetBalance(config) {
  const connection = getSolanaConnection(config);
  const wallet = getWalletKeypair(config);
  const balance = await connection.getBalance(wallet.publicKey);
  return { success: true, balance: balance / 1e9 };
}

// ─── Command: check-range ─────────────────────────────────────
async function cmdCheckRange(config, poolAddressStr, downsidePctStr) {
  const connection = getSolanaConnection(config);
  const poolPubKey = new PublicKey(poolAddressStr);
  const pool = await DLMM.create(connection, poolPubKey);
  const activeBin = await pool.getActiveBin();
  const binStep = pool.lbPair.binStep;
  
  const activePriceUnscaled = Number(activeBin.price.toString());
  const activePriceScaled = Number(pool.fromPricePerLamport(activeBin.price));
  
  let downsidePct = 92;
  if (downsidePctStr) {
    const val = parseFloat(downsidePctStr);
    if (!isNaN(val) && val > 0 && val < 100) {
      downsidePct = val;
    }
  }
  const lowerPriceUnscaled = activePriceUnscaled * (1 - downsidePct / 100);
  const lowerBinId = DLMM.getBinIdFromPrice(lowerPriceUnscaled, binStep, true);
  const totalBins = activeBin.binId - lowerBinId;
  
  // Calculate dynamic rent
  const rentLamports = await getPositionRentExemption(connection, new BN(totalBins));
  const refundableRent = Number(rentLamports) / 1e9;
  
  // Check uninitialized bin arrays
  const programId = new PublicKey("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo");
  const lower = new BN(Math.min(lowerBinId, activeBin.binId));
  const upper = new BN(Math.max(lowerBinId, activeBin.binId));
  
  const indexes = getBinArrayIndexesCoverage(lower, upper);
  const keys = getBinArrayKeysCoverage(lower, upper, poolPubKey, programId);
  const accounts = await connection.getMultipleAccountsInfo(keys, "confirmed");
  
  let missingCount = 0;
  accounts.forEach((account) => {
    if (!account) missingCount++;
  });
  
  const BIN_ARRAY_FEE = 0.07143744;
  const nonRefundableFee = missingCount * BIN_ARRAY_FEE;
  const eligible = missingCount === 0;
  
  return {
    success: true,
    eligible,
    total_bins: totalBins,
    active_bin: activeBin.binId,
    lower_bin: lowerBinId,
    refundable_rent: refundableRent,
    non_refundable_fee: nonRefundableFee,
    missing_arrays: missingCount,
    active_price: activePriceScaled
  };
}

// ─── Command: open ────────────────────────────────────────────
async function cmdOpen(config, poolAddressStr, amountSolStr, downsidePctStr, strategyTypeStr) {
  if (config.dry_run) {
    return { success: true, dry_run: true, message: "Dry run: position would be opened." };
  }
  
  const connection = getSolanaConnection(config);
  const wallet = getWalletKeypair(config);
  const poolPubKey = new PublicKey(poolAddressStr);
  const pool = await DLMM.create(connection, poolPubKey);
  const activeBin = await pool.getActiveBin();
  const binStep = pool.lbPair.binStep;
  
  const activePriceUnscaled = Number(activeBin.price.toString());
  
  let downsidePct = 92;
  if (downsidePctStr) {
    const val = parseFloat(downsidePctStr);
    if (!isNaN(val) && val > 0 && val < 100) {
      downsidePct = val;
    }
  }
  
  const lowerPriceUnscaled = activePriceUnscaled * (1 - downsidePct / 100);

  const lowerBinId = DLMM.getBinIdFromPrice(lowerPriceUnscaled, binStep, true);
  const totalBins = activeBin.binId - lowerBinId;
  
  // Calculate dynamic rent
  const rentLamports = await getPositionRentExemption(connection, new BN(totalBins));
  const refundableRent = Number(rentLamports) / 1e9;
  
  // Verify uninitialized bin arrays
  const programId = new PublicKey("LBUZKhRxPF3XUpBCjp4YzTKgLccjZhTSDM9YuVaPwxo");
  const lower = new BN(Math.min(lowerBinId, activeBin.binId));
  const upper = new BN(Math.max(lowerBinId, activeBin.binId));
  
  const indexes = getBinArrayIndexesCoverage(lower, upper);
  const keys = getBinArrayKeysCoverage(lower, upper, poolPubKey, programId);
  const accounts = await connection.getMultipleAccountsInfo(keys, "confirmed");
  
  let missingCount = 0;
  accounts.forEach((account) => {
    if (!account) missingCount++;
  });
  
  if (missingCount > 0) {
    throw new Error(`Position deploy aborted: ${missingCount} bin arrays require initialization.`);
  }
  
  const amountSol = parseFloat(amountSolStr);
  if (amountSol <= 0) {
    throw new Error("Amount must be greater than 0");
  }
  
  // SOL is token Y
  const totalYLamports = new BN(Math.floor(amountSol * 1e9));
  const totalXLamports = new BN(0);
  
  let strategyType = StrategyType.Spot;
  if (strategyTypeStr) {
    const s = strategyTypeStr.toLowerCase();
    if (s === "bid-ask" || s === "bidask") {
      strategyType = StrategyType.BidAsk;
    } else if (s === "curve") {
      strategyType = StrategyType.Curve;
    }
  }
  
  const newPosition = Keypair.generate();
  const txHashes = [];
  
  const isWideRange = totalBins > 69;
  
  if (isWideRange) {
    // Phase 1: Create empty position
    const createTxs = await pool.createExtendedEmptyPosition(
      lowerBinId,
      activeBin.binId,
      newPosition.publicKey,
      wallet.publicKey
    );
    const createTxArray = Array.isArray(createTxs) ? createTxs : [createTxs];
    for (let i = 0; i < createTxArray.length; i++) {
      const signers = i === 0 ? [wallet, newPosition] : [wallet];
      const txHash = await sendAndConfirmTransactionWithFees(connection, createTxArray[i], signers);
      txHashes.push(txHash);
    }
    
    // Phase 2: Add liquidity
    const addTxs = await pool.addLiquidityByStrategyChunkable({
      positionPubKey: newPosition.publicKey,
      user: wallet.publicKey,
      totalXAmount: totalXLamports,
      totalYAmount: totalYLamports,
      strategy: { minBinId: lowerBinId, maxBinId: activeBin.binId, strategyType },
      slippage: 1000 // 10%
    });
    const addTxArray = Array.isArray(addTxs) ? addTxs : [addTxs];
    for (let i = 0; i < addTxArray.length; i++) {
      const txHash = await sendAndConfirmTransactionWithFees(connection, addTxArray[i], [wallet]);
      txHashes.push(txHash);
    }
  } else {
    // Standard position
    const tx = await pool.initializePositionAndAddLiquidityByStrategy({
      positionPubKey: newPosition.publicKey,
      user: wallet.publicKey,
      totalXAmount: totalXLamports,
      totalYAmount: totalYLamports,
      strategy: { minBinId: lowerBinId, maxBinId: activeBin.binId, strategyType },
      slippage: 1000 // 10%
    });
    const txHash = await sendAndConfirmTransactionWithFees(connection, tx, [wallet, newPosition]);
    txHashes.push(txHash);
  }
  
  return {
    success: true,
    position: newPosition.publicKey.toString(),
    txs: txHashes,
    lower_bin: lowerBinId,
    upper_bin: activeBin.binId,
    refundable_rent: refundableRent
  };
}

// ─── Command: close ───────────────────────────────────────────
async function cmdClose(config, poolAddressStr, positionAddressStr) {
  if (config.dry_run) {
    return { success: true, dry_run: true, message: "Dry run: position would be closed." };
  }
  
  const connection = getSolanaConnection(config);
  const wallet = getWalletKeypair(config);
  const poolPubKey = new PublicKey(poolAddressStr);
  const positionPubKey = new PublicKey(positionAddressStr);
  
  const pool = await DLMM.create(connection, poolPubKey);
  const positionData = await pool.getPosition(positionPubKey);
  
  const processed = positionData?.positionData;
  if (!processed) {
    throw new Error("Could not find position data on-chain.");
  }
  
  const lowerBinId = processed.lowerBinId;
  const upperBinId = processed.upperBinId;
  
  // Claim fees & remove liquidity
  const closeTx = await pool.removeLiquidity({
    user: wallet.publicKey,
    position: positionPubKey,
    fromBinId: lowerBinId,
    toBinId: upperBinId,
    bps: new BN(10000),
    shouldClaimAndClose: true
  });
  
  const txHashes = [];
  const closeTxArray = Array.isArray(closeTx) ? closeTx : [closeTx];
  for (const tx of closeTxArray) {
    const txHash = await sendAndConfirmTransactionWithFees(connection, tx, [wallet]);
    txHashes.push(txHash);
  }
  
  // Check post-withdraw token X balance to return it
  const tokenXMint = pool.lbPair.tokenXMint;
  const tokenXPublicKey = new PublicKey(tokenXMint);
  
  let tokenXAmount = 0;
  try {
    const tokenAccounts = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, {
      mint: tokenXPublicKey
    });
    if (tokenAccounts.value && tokenAccounts.value.length > 0) {
      const balanceObj = tokenAccounts.value[0].account.data.parsed.info.tokenAmount;
      tokenXAmount = balanceObj.uiAmount || 0;
    }
  } catch (err) {
    console.error("Warning: could not parse token X balance:", err.message);
  }
  
  return {
    success: true,
    txs: txHashes,
    token_x_mint: tokenXMint.toString(),
    token_x_amount: tokenXAmount
  };
}

// ─── Command: swap ────────────────────────────────────────────
async function cmdSwap(config, inputMint, outputMint, amountStr) {
  if (config.dry_run) {
    return { success: true, dry_run: true, message: `Dry run: swap ${amountStr} ${inputMint} -> ${outputMint}` };
  }
  
  const connection = getSolanaConnection(config);
  const wallet = getWalletKeypair(config);
  
  const amount = parseFloat(amountStr);
  if (amount <= 0) {
    throw new Error("Amount must be greater than 0");
  }
  
  // Convert amount to smallest unit
  let decimals = 9; // default SOL
  const SOL_MINT = "So11111111111111111111111111111111111111112";
  if (inputMint !== SOL_MINT && inputMint !== "SOL") {
    const mintInfo = await connection.getParsedAccountInfo(new PublicKey(inputMint));
    decimals = mintInfo.value?.data?.parsed?.info?.decimals ?? 9;
  }
  
  const rawAmountStr = Math.floor(amount * Math.pow(10, decimals)).toString();
  
  const searchParams = new URLSearchParams({
    inputMint: inputMint === "SOL" ? SOL_MINT : inputMint,
    outputMint: outputMint === "SOL" ? SOL_MINT : outputMint,
    amount: rawAmountStr,
    taker: wallet.publicKey.toString()
  });
  
  const JUPITER_API_URL = `https://api.jup.ag/swap/v2/order?${searchParams.toString()}`;
  const res = await fetch(JUPITER_API_URL);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`Jupiter Order API failed: ${res.status} - ${body}`);
  }
  
  const order = await res.json();
  if (order.errorCode || order.errorMessage) {
    throw new Error(`Jupiter Order Error: ${order.errorMessage || order.errorCode}`);
  }
  
  const { transaction: unsignedTx, requestId } = order;
  
  // Sign transaction
  const tx = VersionedTransaction.deserialize(Buffer.from(unsignedTx, "base64"));
  tx.sign([wallet]);
  const signedTx = Buffer.from(tx.serialize()).toString("base64");
  
  // Execute transaction
  const execRes = await fetch("https://api.jup.ag/swap/v2/execute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ signedTransaction: signedTx, requestId })
  });
  if (!execRes.ok) {
    throw new Error(`Jupiter Execute API failed: ${execRes.status} - ${await execRes.text()}`);
  }
  
  const result = await execRes.json();
  if (result.status === "Failed") {
    throw new Error(`Jupiter transaction failed on-chain: code=${result.code}`);
  }
  
  return {
    success: true,
    tx: result.signature,
    amount_in: amount,
    amount_out_raw: result.outputAmountResult
  };
}

// ─── Command: get-helius-credits ──────────────────────────────
async function cmdGetHeliusCredits(config) {
  if (!config.wallet_private_key || config.wallet_private_key.startsWith("YOUR_")) {
    throw new Error("Wallet private key not configured");
  }
  
  const secretKey = bs58.decode(config.wallet_private_key);
  const keypair = Keypair.fromSecretKey(secretKey);
  const address = keypair.publicKey.toBase58();
  
  const timestamp = Date.now();
  const messageObj = {
    message: "Please sign this message to verify ownership of your wallet and connect to Helius.",
    timestamp: timestamp
  };
  const messageStr = JSON.stringify(messageObj);
  const messageBytes = new TextEncoder().encode(messageStr);
  const signatureBytes = ed25519.sign(messageBytes, keypair.secretKey.slice(0, 32));
  const signatureStr = bs58.encode(signatureBytes);
  
  const signupRes = await fetch("https://dev-api.helius.xyz/v0/wallet-signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: messageStr,
      signature: signatureStr,
      userID: address
    })
  });
  
  if (!signupRes.ok) {
    throw new Error(`Helius wallet-signup failed: ${await signupRes.text()}`);
  }
  
  const signupData = await signupRes.json();
  const jwt = signupData.token;
  
  const projectsRes = await fetch("https://dev-api.helius.xyz/v0/projects", {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${jwt}`
    }
  });
  
  if (!projectsRes.ok) {
    throw new Error(`Helius projects fetch failed: ${await projectsRes.text()}`);
  }
  
  const projects = await projectsRes.json();
  if (projects.length === 0) {
    throw new Error("No projects found on this Helius account");
  }
  
  const projectId = projects[0].id;
  const projectRes = await fetch(`https://dev-api.helius.xyz/v0/projects/${projectId}`, {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${jwt}`
    }
  });
  
  if (!projectRes.ok) {
    throw new Error(`Helius project details fetch failed: ${await projectRes.text()}`);
  }
  
  const details = await projectRes.json();
  const usage = details.creditsUsage;
  if (!usage) {
    throw new Error("No usage details found in project response");
  }
  
  return {
    success: true,
    used: usage.totalCreditsUsed,
    remaining: usage.remainingCredits,
    limit: usage.totalCreditsUsed + usage.remainingCredits
  };
}

// ─── Command: get-active-positions ─────────────────────────────
async function cmdGetActivePositions(config) {
  const connection = getSolanaConnection(config);
  const wallet = getWalletKeypair(config);
  
  // We initialize the program by creating a DLMM instance for a dummy pool
  const dummyPoolPubKey = new PublicKey("BRHNunv6MzznkDQAev7aEBmQANudm7NC44MuLVz6FMf6");
  const dummyPool = await DLMM.create(connection, dummyPoolPubKey);
  const program = dummyPool.program;
  
  const positions = await program.account.positionV2.all([
    {
      memcmp: {
        offset: 40, // 8 discriminator + 32 lbPair = 40
        bytes: wallet.publicKey.toBase58()
      }
    }
  ]);
  
  const result = [];
  for (const pos of positions) {
    try {
      const positionPubKey = pos.publicKey;
      const poolPubKey = pos.account.lbPair;
      
      const pool = await DLMM.create(connection, poolPubKey);
      const mintX = pool.lbPair.tokenXMint.toBase58();
      const mintY = pool.lbPair.tokenYMint.toBase58();
      const wsolMint = "So11111111111111111111111111111111111111112";
      
      let tokenAddress = mintX;
      let symbol = (pool.tokenX && pool.tokenX.symbol) ? pool.tokenX.symbol : "UNKNOWN";
      
      if (mintX === wsolMint) {
        tokenAddress = mintY;
        symbol = (pool.tokenY && pool.tokenY.symbol) ? pool.tokenY.symbol : "UNKNOWN";
      }
      
      const lowerBin = pos.account.lowerBinId;
      const upperBin = pos.account.upperBinId;
      const totalBins = upperBin - lowerBin;
      const rentLamports = await getPositionRentExemption(connection, new BN(totalBins));
      const refundableRent = Number(rentLamports) / 1e9;
      
      const positionData = await pool.getPosition(positionPubKey);
      const processed = positionData?.positionData;
      let feeSol = 0;
      if (processed) {
        if (mintX === wsolMint) {
          feeSol = Number(processed.feeX.toString()) / 1e9;
        } else if (mintY === wsolMint) {
          feeSol = Number(processed.feeY.toString()) / 1e9;
        }
      }
      
      result.push({
        token_address: tokenAddress,
        symbol: symbol,
        pool_address: poolPubKey.toBase58(),
        position_address: positionPubKey.toBase58(),
        lower_bin: lowerBin,
        upper_bin: upperBin,
        refundable_rent: refundableRent,
        fee_sol: feeSol
      });
    } catch (e) {
      console.error(`Error parsing position ${pos.publicKey.toBase58()}:`, e.message);
    }
  }
  
  return { success: true, positions: result };
}


// ─── Command: swap-remaining-tokens ───────────────────────────
async function cmdSwapRemainingTokens(config) {
  const connection = getSolanaConnection(config);
  const wallet = getWalletKeypair(config);
  
  const tokenProgramId = new PublicKey("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
  const token2022ProgramId = new PublicKey("TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCX8a56kk");
  
  const accounts = [];
  try {
    const res1 = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, { programId: tokenProgramId }, "confirmed");
    accounts.push(...res1.value);
  } catch (e) {
    console.error("Failed to fetch Token accounts:", e.message);
  }
  
  try {
    const res2 = await connection.getParsedTokenAccountsByOwner(wallet.publicKey, { programId: token2022ProgramId }, "confirmed");
    accounts.push(...res2.value);
  } catch (e) {
    console.error("Failed to fetch Token-2022 accounts:", e.message);
  }
  
  const swaps = [];
  const WSOL_MINT = "So11111111111111111111111111111111111111112";
  
  for (const account of accounts) {
    const info = account.account.data.parsed.info;
    const mint = info.mint;
    const amount = info.tokenAmount.uiAmount;
    const decimals = info.tokenAmount.decimals;
    const rawAmount = info.tokenAmount.amount;
    
    if (amount <= 0) continue;
    if (mint === WSOL_MINT) continue; // Skip WSOL
    
    try {
      if (config.dry_run) {
        swaps.push({ mint, amount, dry_run: true, message: "Dry run: would swap to SOL" });
        continue;
      }
      
      const searchParams = new URLSearchParams({
        inputMint: mint,
        outputMint: "SOL",
        amount: rawAmount,
        taker: wallet.publicKey.toString()
      });
      
      const JUPITER_API_URL = `https://api.jup.ag/swap/v2/order?${searchParams.toString()}`;
      const res = await fetch(JUPITER_API_URL);
      if (!res.ok) {
        console.error(`Token ${mint} swap quote failed: ${res.status}`);
        continue;
      }
      
      const order = await res.json();
      if (order.errorCode || order.errorMessage) {
        console.error(`Token ${mint} Jupiter error: ${order.errorMessage || order.errorCode}`);
        continue;
      }
      
      const { transaction: unsignedTx, requestId } = order;
      const tx = VersionedTransaction.deserialize(Buffer.from(unsignedTx, "base64"));
      tx.sign([wallet]);
      const signedTx = Buffer.from(tx.serialize()).toString("base64");
      
      const execRes = await fetch("https://api.jup.ag/swap/v2/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ signedTransaction: signedTx, requestId })
      });
      
      if (!execRes.ok) {
        console.error(`Token ${mint} swap execution failed: ${execRes.status}`);
        continue;
      }
      
      const result = await execRes.json();
      if (result.status === "Failed") {
        console.error(`Token ${mint} transaction failed on-chain`);
        continue;
      }
      
      swaps.push({ mint, amount, success: true, tx: result.signature });
    } catch (err) {
      console.error(`Error swapping ${mint}:`, err.message);
    }
  }
  
  return { success: true, swaps };
}

// ─── Entry Point ──────────────────────────────────────────────
async function main() {
  const args = process.argv.slice(2);
  if (args.length === 0) {
    console.log(JSON.stringify({ success: false, error: "No command provided." }));
    process.exit(1);
  }
  
  const command = args[0];
  const config = readConfig();
  
  let result;
  switch (command) {
    case "get-balance":
      result = await cmdGetBalance(config);
      break;
    case "check-range":
      if (args.length < 2) throw new Error("Missing pool address for check-range");
      result = await cmdCheckRange(config, args[1], args[2]);
      break;
    case "open":
      if (args.length < 3) throw new Error("Missing arguments for open: <pool_address> <amount_sol> [downside_pct] [strategy_type]");
      result = await cmdOpen(config, args[1], args[2], args[3], args[4]);
      break;
    case "close":
      if (args.length < 3) throw new Error("Missing arguments for close: <pool_address> <position_address>");
      result = await cmdClose(config, args[1], args[2]);
      break;
    case "swap":
      if (args.length < 4) throw new Error("Missing arguments for swap: <input_mint> <output_mint> <amount>");
      result = await cmdSwap(config, args[1], args[2], args[3]);
      break;
    case "get-helius-credits":
      result = await cmdGetHeliusCredits(config);
      break;
    case "swap-remaining-tokens":
      result = await cmdSwapRemainingTokens(config);
      break;
    case "get-active-positions":
      result = await cmdGetActivePositions(config);
      break;
    default:
      throw new Error(`Unknown command: ${command}`);
  }
  
  console.log(JSON.stringify(result));
}

main().catch((err) => {
  console.log(JSON.stringify({ success: false, error: err.message }));
  process.exit(1);
});
