export default function CoreRing() {
  return (
    <div className="flex flex-col items-center justify-center">
      <div className="relative w-[500px] h-[500px]">

        {/* Outer Ring */}
        <svg
          className="absolute inset-0 animate-spin-slow"
          viewBox="0 0 500 500"
        >
          <circle
            cx="250"
            cy="250"
            r="210"
            stroke="#00e5ff"
            strokeWidth="3"
            fill="none"
            strokeDasharray="20 10"
          />
        </svg>

        {/* Middle Ring */}
        <svg
          className="absolute inset-0 animate-spin-reverse"
          viewBox="0 0 500 500"
        >
          <circle
            cx="250"
            cy="250"
            r="170"
            stroke="#00ffff"
            strokeWidth="2"
            fill="none"
            strokeDasharray="10 6"
          />
        </svg>

        {/* Inner Ring */}
        <svg
          className="absolute inset-0 animate-pulse"
          viewBox="0 0 500 500"
        >
          <circle
            cx="250"
            cy="250"
            r="120"
            stroke="#00c8ff"
            strokeWidth="3"
            fill="none"
          />
        </svg>

        {/* Core */}
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-40 h-40 rounded-full bg-cyan-500 shadow-[0_0_60px_#00ffff] flex items-center justify-center">
            <div className="text-center">
              <h1 className="text-5xl font-bold text-black">HS</h1>
              <p className="text-black text-sm font-bold">ACTIVE</p>
            </div>
          </div>
        </div>
      </div>

      <h2 className="text-cyan-400 text-3xl font-bold mt-4">
        HSDEEP CORE AI
      </h2>
      <p className="text-cyan-600">
        Autonomous Infrastructure Intelligence
      </p>
    </div>
  );
}