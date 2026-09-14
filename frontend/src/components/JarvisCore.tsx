"use client";

export default function JarvisCore() {
  return (
    <div className="flex justify-center items-center py-12">
      <div className="relative w-[350px] h-[350px] flex items-center justify-center">

        <div className="absolute w-[350px] h-[350px] rounded-full border-4 border-cyan-500 animate-spin opacity-70"></div>

        <div
          className="absolute w-[270px] h-[270px] rounded-full border-4 border-cyan-300 opacity-70"
          style={{
            animation: "spinReverse 8s linear infinite",
          }}
        ></div>

        <div className="absolute w-[180px] h-[180px] rounded-full border-2 border-cyan-400 animate-pulse"></div>

        <div className="w-[120px] h-[120px] rounded-full bg-cyan-500 shadow-[0_0_50px_#06b6d4] flex items-center justify-center text-black font-bold text-xl">
          AI
        </div>

        <div className="absolute bottom-[-50px] text-cyan-300 text-xl font-semibold">
          J.A.R.V.I.S CORE
        </div>
      </div>
    </div>
  );
}