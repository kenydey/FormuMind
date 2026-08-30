import { useEffect, useState } from "react";
import type { ObjectiveSpec, ProductDomain } from "../api";
import { api } from "../api";

interface DoeProgressCardProps {
  targetId: string;
  domain: ProductDomain;
  objectives: ObjectiveSpec[];
  totalRounds: number;
}

export default function DoeProgressCard({ targetId, domain: _domain, objectives: _objectives, totalRounds }: DoeProgressCardProps) {
  const [completedRounds, setCompletedRounds] = useState(0);
  const [bestValue, setBestValue] = useState<number | null>(null);
  const [confidenceInterval, setConfidenceInterval] = useState<{ lower: number; upper: number } | null>(null);
  const [remainingExperiments, setRemainingExperiments] = useState<number | null>(null);
  const [isPaused, setIsPaused] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Subscribe to SSE for real-time updates
    const eventSource = new EventSource(`/api/experiments/hooks/progress/${targetId}`);

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setCompletedRounds(data.completedRounds || 0);
        setBestValue(data.bestValue);
        setConfidenceInterval(data.confidenceInterval);
        setRemainingExperiments(data.remainingExperiments);
        setIsPaused(data.isPaused ?? false);
        setIsLoading(false);
      } catch (e) {
        console.error("Failed to parse DOE progress event:", e);
      }
    };

    eventSource.onerror = (error) => {
      console.error("DOE progress SSE error:", error);
      setError("Failed to connect to progress updates");
      setIsLoading(false);
    };

    // Cleanup on unmount
    return () => {
      eventSource.close();
    };
  }, [targetId]);

  const handleTogglePause = async () => {
    try {
      await api.postDoeCyclePause(targetId, !isPaused);
      setIsPaused(!isPaused);
    } catch (e) {
      console.error("Failed to toggle DOE cycle pause:", e);
      setError("Failed to update pause status");
    }
  };

  if (isLoading) {
    return (
      <div className="p-4 bg-gray-50 rounded-lg border border-gray-200">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-gray-800">DOE Progress</h3>
          <div className="animate-spin h-4 w-4 border-2 border-blue-500 border-t-transparent rounded-full"></div>
        </div>
        <p className="text-gray-500 text-sm">Initializing DOE optimization...</p>
      </div>
    );
  }

  if (error) {
    return (
      <div className="p-4 bg-red-50 rounded-lg border border-red-200">
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-red-800">DOE Progress</h3>
          <button onClick={handleTogglePause} className="text-sm text-blue-600 hover:text-blue-800">
            {isPaused ? "Resume" : "Pause"}
          </button>
        </div>
        <p className="text-red-600 text-sm">{error}</p>
      </div>
    );
  }

  const formatValue = (val: number | null): string => {
    if (val === null) return "--";
    return val.toFixed(2);
  };

  return (
    <div className="p-4 bg-white rounded-lg border border-gray-200 shadow-sm">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-gray-800 flex items-center">
          DOE Progress
          <span className="ml-2 text-xs text-gray-500">(Target: {totalRounds} rounds)</span>
        </h3>
        <button
          onClick={handleTogglePause}
          className={`px-3 py-1 text-sm rounded ${
            isPaused
              ? "bg-green-100 text-green-800 hover:bg-green-200"
              : "bg-red-100 text-red-800 hover:bg-red-200"
          }`}
        >
          {isPaused ? "Resume" : "Pause"}
        </button>
      </div>

      <div className="space-y-3">
        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">Completed Rounds:</span>
          <span className="font-mono">{completedRounds}/{totalRounds}</span>
        </div>

        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">Best Value:</span>
          <span className="font-mono">{formatValue(bestValue)}</span>
        </div>

        {confidenceInterval && (
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium">95% CI:</span>
            <span className="font-mono">
              [{formatValue(confidenceInterval.lower)}, {formatValue(confidenceInterval.upper)}]
            </span>
          </div>
        )}

        <div className="flex items-center justify-between text-sm">
          <span className="font-medium">Remaining:</span>
          <span className="font-mono">
            {remainingExperiments !== null ? remainingExperiments : "?"}
          </span>
        </div>

        <div className="w-full bg-gray-200 rounded-full h-2.5">
          <div
            className="bg-blue-600 h-2.5 rounded-full"
            style={{ width: `${(completedRounds / totalRounds) * 100}%` }}
          ></div>
        </div>

        <div className="flex items-center justify-between text-xs text-gray-500">
          <span>Progress: {Math.round((completedRounds / totalRounds) * 100)}%</span>
        </div>
      </div>
    </div>
  );
}