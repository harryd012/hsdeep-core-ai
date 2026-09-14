export default function LiveFeed() {
  return (
    <div className="glass p-6 min-h-[250px]">
      <h2 className="font-bold mb-4">LIVE TASK FEED</h2>

      <div className="space-y-4 text-sm">
        <div>05:41 → PRTG Agent checking WAN links</div>
        <div>05:40 → SOC Agent correlating alerts</div>
        <div>05:39 → Azure Agent investigating CPU spike</div>
      </div>
    </div>
  );
}