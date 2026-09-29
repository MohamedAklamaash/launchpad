// CI-only Next build adapter. Vercel always builds through a build adapter, and adapter
// builds take code paths a plain `next build` never hits (Next 16.3.0–16.3.4 failed in
// onBuildComplete with ENOENT .next/next-server.js.nft.json under output: standalone,
// breaking every Vercel deploy while CI stayed green). CI sets NEXT_ADAPTER_PATH to this
// file so the build step exercises the same path.
module.exports = {
  name: "launchpad-ci-noop-adapter",
  async onBuildComplete() {},
};
